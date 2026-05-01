"""
Huntova SaaS — Per-user agent context
Replaces all globals with per-user isolated state.
"""
import asyncio
import queue
import threading
import time
import json
from datetime import datetime, timezone
from openai import OpenAI

from config import API_URL, API_KEY, MODEL_ID


class UserEventBus:
    """Per-user SSE event bus. Thread-safe: emit() called from agent threads,
    subscribe/get called from asyncio event loop."""
    # Terminal status states — cached and replayed to new subscribers so a
    # just-ended agent's final state is visible to admin viewers that connect
    # seconds after the run ends.
    _TERMINAL_STATES = {"idle", "stopped", "error", "exhausted", "completed"}

    # Stability fix (multi-agent bug #11): a queue whose size has crossed
    # half its capacity is almost certainly attached to a dead/disconnected
    # client — a healthy SSE consumer drains as fast as we put. Drop early
    # so we don't have to wait for it to fill all the way to maxsize before
    # the existing put_nowait-Full path kicks it out.
    _MAXSIZE = 200
    _DEAD_THRESHOLD = 100

    def __init__(self):
        self._subscribers: set[queue.Queue] = set()
        self._lock = threading.Lock()
        self._last_terminal: str | None = None
        # Stability fix (Perplexity bug #39): also cache the latest
        # non-status snapshots clients need to render correct UI on
        # reconnect — without this, an SSE drop in the middle of a run
        # left the user's UI showing whatever progress they last saw
        # locally until the next emit, which can be tens of seconds.
        self._last_progress: str | None = None
        self._last_running_status: str | None = None

    def subscribe(self) -> queue.Queue:
        q = queue.Queue(maxsize=self._MAXSIZE)
        with self._lock:
            # Order matters: status first so the UI updates "running" /
            # "queued" before the counter snapshot lands; terminal
            # status overrides running status if a run just ended.
            for cached in (self._last_running_status, self._last_progress, self._last_terminal):
                if cached is None:
                    continue
                try:
                    q.put_nowait(cached)
                except queue.Full:
                    break
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: queue.Queue):
        with self._lock:
            self._subscribers.discard(q)

    def emit(self, event: str, data: dict):
        msg = f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
        dead = []
        with self._lock:
            # Cache terminal status for replay to future subscribers; clear
            # on any non-terminal status so a restart doesn't replay stale
            # "idle" to new clients.
            if event == "status" and isinstance(data, dict):
                state = data.get("state")
                if state in self._TERMINAL_STATES:
                    self._last_terminal = msg
                    # Run ended — clear the running snapshots so a
                    # reconnect doesn't see "running" + stale counters
                    # alongside the terminal frame.
                    self._last_running_status = None
                    self._last_progress = None
                else:
                    self._last_terminal = None
                    self._last_running_status = msg
            elif event == "progress":
                # Mid-run progress snapshot — replayed on reconnect so
                # the UI shows the right counters immediately.
                self._last_progress = msg
            for q in list(self._subscribers):
                try:
                    q.put_nowait(msg)
                except queue.Full:
                    dead.append(q)
                    continue
                # Early dead-client detection — see _DEAD_THRESHOLD.
                if q.qsize() >= self._DEAD_THRESHOLD:
                    dead.append(q)
            for q in dead:
                self._subscribers.discard(q)
            # Belt-and-braces sweep: any subscriber whose queue is at or
            # above the dead threshold gets purged regardless of whether
            # this emit() touched it. Without this, a queue that filled
            # to maxsize on emit #1 and then drained one slot before
            # emit #2 would stay in the set forever (qsize >= threshold
            # but never raises queue.Full again because put_nowait
            # succeeds), wasting cycles on every subsequent emit.
            for q in list(self._subscribers):
                if q.qsize() >= self._DEAD_THRESHOLD:
                    self._subscribers.discard(q)


class UserAgentContext:
    """Per-user agent state — replaces all module-level globals from app.py."""

    def __init__(self, user_id: int, user_email: str, user_tier: str = "free"):
        self.user_id = user_id
        self.user_email = user_email
        self.user_tier = user_tier

        # AI client (shared Gemini key)
        self.client = OpenAI(base_url=API_URL, api_key=API_KEY)

        # Per-user event bus
        self.bus = UserEventBus()

        # Agent control
        self.agent_running = False
        self.agent_ctrl = {"action": None}
        self.agent_config = {"countries": [], "max_queries": 310, "results_per_query": 5}
        self.agent_ctrl_lock = threading.Lock()

        # Run state
        self.seen_urls: set = set()
        self.seen_fps: set = set()
        self.domain_fails: dict = {}
        self.all_leads: list = []
        self.save_done = False
        self.found_domains: list = []
        self.current_session_log = ""

        # Agent run tracking
        self.run_id: int | None = None
        self.run_ts = ""
        self.credits_used = 0

        # Latest progress snapshot — mirrored here so /api/status can return
        # live counters to clients that reconnect after an SSE drop.
        self._latest_progress: dict | None = None

        # Session log file handle. Held open for the duration of one run so
        # emit_log doesn't open()/close() the file on every log line — at
        # ~thousands of lines per run that adds up to real syscall overhead
        # under load. Opened lazily on first write, closed in
        # close_session_log() from the agent thread's finally block.
        self._session_log_fh = None
        self._session_log_path_open = ""
        self._session_log_lock = threading.Lock()
        self._session_log_disabled = False

    def check_stop(self) -> bool:
        with self.agent_ctrl_lock:
            return self.agent_ctrl.get("action") == "stop"

    def check_pause(self) -> bool:
        while True:
            with self.agent_ctrl_lock:
                act = self.agent_ctrl.get("action")
                if act == "stop":
                    return True
                if act != "pause":
                    return False
            time.sleep(0.5)

    def emit_log(self, msg: str, level: str = "info"):
        icons = {"info": "🔍", "ok": "✅", "warn": "⚠️", "lead": "⭐", "skip": "⏭",
                 "fetch": "📄", "ai": "🤖", "save": "💾", "error": "❌"}
        icon = icons.get(level, "🔍")
        ts = datetime.now().strftime("%H:%M:%S")
        text = f"{ts} {icon} {msg}"
        self.bus.emit("log", {"msg": text, "level": level, "ts": ts})
        # Collect for run log file. Cap at 1000 entries — this is the
        # in-memory tail kept for the post-run summary; the on-disk
        # session log keeps the full record. A 1000-lead run can emit
        # 5000+ lines, which then linger in RAM for the lifetime of
        # the per-user context. Trim oldest 200 when we hit the cap so
        # the eviction is amortised, not on every line.
        if not hasattr(self, '_run_log'):
            self._run_log = []
        self._run_log.append(f"[{ts}] [{level.upper():5s}] {msg}")
        if len(self._run_log) > 1000:
            del self._run_log[:200]
        # Write to session log via cached file handle.
        # Stability fix (multi-agent bug #10): previously this opened the
        # log file on every emit_log call — over a 1000-lead run that's
        # thousands of open()/close() syscalls. Hold the handle open for
        # the duration of the run; close in close_session_log() from the
        # agent thread's finally block.
        if self._session_log_disabled or not self.current_session_log:
            return
        line = f"[{ts}] [{level.upper():5s}] {msg}\n"
        with self._session_log_lock:
            try:
                if self._session_log_fh is None or self._session_log_path_open != self.current_session_log:
                    if self._session_log_fh is not None:
                        try:
                            self._session_log_fh.close()
                        except Exception:
                            pass
                    # buffering=1 → line-buffered, so each log line is flushed
                    # to disk without requiring an explicit flush() per write.
                    self._session_log_fh = open(self.current_session_log, "a", encoding="utf-8", buffering=1)
                    self._session_log_path_open = self.current_session_log
                self._session_log_fh.write(line)
            except Exception as _err:
                # Disable for the rest of the run rather than re-failing on
                # every subsequent log line. SSE bus already got the line.
                self._session_log_disabled = True
                print(f"[user_context] session log disabled for user {self.user_id}: {_err}")
                try:
                    if self._session_log_fh is not None:
                        self._session_log_fh.close()
                except Exception:
                    pass
                self._session_log_fh = None

    def close_session_log(self):
        """Close the cached session log handle. Call from agent thread finally."""
        with self._session_log_lock:
            if self._session_log_fh is not None:
                try:
                    self._session_log_fh.close()
                except Exception:
                    pass
                self._session_log_fh = None
                self._session_log_path_open = ""
            # Reset disable flag so the next run can try again.
            self._session_log_disabled = False

    def emit_progress(self, **kw):
        self._latest_progress = dict(kw)
        self.bus.emit("progress", kw)

    def emit_status(self, text: str, state: str = "running"):
        # Reset the cached progress snapshot when the run reaches a
        # terminal state. Without this, `/api/status` keeps returning
        # the previous run's counters until a new hunt starts and
        # overwrites them — confusing pulse stats and the dashboard
        # status pill.
        if state in ("idle", "stopped", "error", "exhausted", "completed"):
            self._latest_progress = None
        self.bus.emit("status", {"text": text, "state": state})

    def emit_lead(self, lead: dict):
        self.bus.emit("lead", lead)

    def emit_thought(self, msg: str, mood: str = "thinking"):
        self.bus.emit("thought", {"msg": msg, "mood": mood})

    def emit_screenshot(self, page, url: str = ""):
        try:
            import base64
            raw = page.screenshot(type="jpeg", quality=55, full_page=False, timeout=3000)
            b64 = base64.b64encode(raw).decode("ascii")
            self.bus.emit("screenshot", {"img": b64, "url": url[:200], "ts": time.strftime("%H:%M:%S")})
        except Exception:
            pass


# Registry of active user contexts
_active_contexts: dict[int, UserAgentContext] = {}
_contexts_lock = threading.Lock()


def get_or_create_context(user_id: int, user_email: str = "", user_tier: str = "") -> UserAgentContext:
    with _contexts_lock:
        if user_id not in _active_contexts:
            _active_contexts[user_id] = UserAgentContext(user_id, user_email, user_tier or "free")
        else:
            # Only update tier/email if explicitly provided (not default empty string)
            ctx = _active_contexts[user_id]
            if user_tier and user_tier != ctx.user_tier:
                ctx.user_tier = user_tier
            if user_email and user_email != ctx.user_email:
                ctx.user_email = user_email
        return _active_contexts[user_id]


def get_context(user_id: int) -> UserAgentContext | None:
    with _contexts_lock:
        return _active_contexts.get(user_id)


def remove_context(user_id: int):
    with _contexts_lock:
        _active_contexts.pop(user_id, None)
