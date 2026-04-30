"""
Huntova SaaS — Multi-user agent orchestration
Manages concurrent agent runs with queue system.
"""
import asyncio
import threading
import time
import traceback
from collections import deque
from datetime import datetime, timezone

import db
from config import MAX_CONCURRENT_AGENTS, LOG_DIR, MAX_RESULTS_PER_QUERY
from user_context import UserAgentContext, get_or_create_context, remove_context


class AgentRunner:
    """Manages agent runs across multiple users."""

    def __init__(self):
        self._running: dict[int, threading.Thread] = {}  # user_id -> thread
        self._queue: deque[int] = deque()  # user_ids waiting
        self._lock = threading.Lock()

    @property
    def running_count(self) -> int:
        with self._lock:
            return len(self._running)

    def is_running(self, user_id: int) -> bool:
        with self._lock:
            thread = self._running.get(user_id)
            if thread is None:
                return False
            if not thread.is_alive():
                # Thread died before finally block could clean up (OS kill, OOM).
                # Remove the ghost entry so admin + UI reflect reality.
                self._running.pop(user_id, None)
                return False
            return True

    def queue_position(self, user_id: int) -> int | None:
        with self._lock:
            if user_id in self._running:
                return 0
            try:
                return list(self._queue).index(user_id) + 1
            except ValueError:
                return None

    async def start_agent(self, user_id: int, user_email: str, user_tier: str,
                          config: dict) -> dict:
        """Request to start agent for user. Returns status."""
        # Billing/policy gate: in local CLI mode this short-circuits
        # to allowed (the user pays their own provider). In cloud
        # mode it preserves the credit precheck.
        from policy import policy
        if policy.deduct_on_save():
            credits = await db.check_and_reset_credits(user_id)
            if credits <= 0:
                return {"ok": False, "error": "No credits remaining. Upgrade your plan."}

        # Health check removed from async start path — blocking sync AI call
        # inside async context causes hangs. Agent thread handles Gemini failures
        # gracefully via retry logic and emits error status via SSE if AI is down.

        ctx = get_or_create_context(user_id, user_email, user_tier)

        with self._lock:
            # Stability fix (round-3 multi-agent + round-8 Perplexity review):
            # self-clean dead/ghost threads BEFORE size + presence checks so a
            # crashed prior thread (no finally cleanup) doesn't permanently
            # prevent the user from restarting AND doesn't waste a slot
            # against MAX_CONCURRENT_AGENTS.
            stale_users = [
                uid for uid, t in self._running.items() if not t.is_alive()
            ]
            for uid in stale_users:
                self._running.pop(uid, None)

            if user_id in self._running:
                return {"ok": False, "error": "Agent already running"}

            # Apply config — backend owns batch sizes, frontend only sends countries.
            # Stability fix (multi-agent bug #20): the previous version
            # blindly trusted whatever the client posted for
            # results_per_query and countries. A hostile or buggy client
            # could send results_per_query=9999 and trigger a self-DoS
            # via SearXNG, or post a giant countries list that bloats
            # every query. Validate at the trust boundary.
            _raw_countries = config.get("countries", []) or []
            if not isinstance(_raw_countries, list):
                _raw_countries = []
            # Cap to 30 countries; only keep short, alphabetic strings
            _safe_countries = [
                str(c)[:50] for c in _raw_countries[:30]
                if isinstance(c, str) and c.strip()
            ]
            try:
                _rpp = int(config.get("results_per_query", MAX_RESULTS_PER_QUERY))
            except (TypeError, ValueError):
                _rpp = MAX_RESULTS_PER_QUERY
            # Clamp to [1, 20] — bigger than 20 is wasteful for everyone.
            _rpp = max(1, min(20, _rpp))
            # Hunt budget caps — None means unlimited (current default).
            # Server-side validation is the source of truth: max_leads
            # ∈ [1, 500], timeout_minutes ∈ [1, 120]. Out-of-range or
            # malformed values silently fall through to unlimited so a
            # buggy client can't crash the agent.
            _max_leads = config.get("max_leads")
            try:
                _max_leads = int(_max_leads) if _max_leads not in (None, "", 0) else None
                if _max_leads is not None and not (1 <= _max_leads <= 500):
                    _max_leads = None
            except (TypeError, ValueError):
                _max_leads = None
            _timeout_min = config.get("timeout_minutes")
            try:
                _timeout_min = int(_timeout_min) if _timeout_min not in (None, "", 0) else None
                if _timeout_min is not None and not (1 <= _timeout_min <= 120):
                    _timeout_min = None
            except (TypeError, ValueError):
                _timeout_min = None
            ctx.agent_config = {
                "countries": _safe_countries,
                "results_per_query": _rpp,
                "max_leads": _max_leads,
                "timeout_minutes": _timeout_min,
                # Wall-clock start used by the agent loop's budget check.
                # Stamped here (not in the agent thread) so a delayed
                # thread spawn doesn't shorten the user's window.
                "started_at": time.time(),
            }

            if len(self._running) < MAX_CONCURRENT_AGENTS:
                # Stability fix (Perplexity bug #60): drop stale queue entry
                # so _process_queue doesn't later spawn a duplicate thread.
                try:
                    self._queue.remove(user_id)
                except ValueError:
                    pass
                # Stability fix (Perplexity bug #64): the previous
                # version did `ctx.agent_ctrl = {"action": None}` —
                # wholesale-clobbering any "stop" action a user had
                # queued via stop_agent while they were waiting. If
                # their slot opens up between Stop and now, we'd start
                # the agent anyway and silently ignore the stop. Now
                # we read the existing ctrl under its lock and bail
                # without spawning if a stop is pending.
                _existing_ctrl = getattr(ctx, "agent_ctrl", None)
                with ctx.agent_ctrl_lock:
                    if isinstance(_existing_ctrl, dict) and _existing_ctrl.get("action") == "stop":
                        # User asked us to stop — clear the flag and bail.
                        _existing_ctrl["action"] = None
                        ctx.agent_running = False
                        _start_result = {"ok": True, "status": "stopped"}
                        _queue_emit = None
                        # Skip the thread.start() block below by using
                        # a sentinel — done after the lock is released.
                        thread = None
                    else:
                        # Fresh-start ctrl, but reuse the dict object if
                        # one already exists so any external readers
                        # holding the same reference see the reset.
                        if isinstance(_existing_ctrl, dict):
                            _existing_ctrl["action"] = None
                        else:
                            ctx.agent_ctrl = {"action": None}
                        ctx.agent_running = True
                        thread = threading.Thread(target=self._run_agent_thread, args=(user_id,), daemon=True)
                if thread is not None:
                    try:
                        self._running[user_id] = thread
                        thread.start()
                    except Exception:
                        self._running.pop(user_id, None)
                        ctx.agent_running = False
                        raise
                    _start_result = {"ok": True, "status": "started"}
                    _queue_emit = None
            else:
                # Queue it. Stability fix (multi-agent bug #33): the
                # previous version called ctx.emit_status WHILE holding
                # self._lock — direct violation of CLAUDE.md rule #9
                # ("Never emit SSE events while holding AgentRunner._lock
                # — causes deadlock"). Match the pattern _process_queue
                # already uses: compute the message inside the lock,
                # emit AFTER the lock is released.
                if user_id not in self._queue:
                    self._queue.append(user_id)
                pos = list(self._queue).index(user_id) + 1
                _start_result = {"ok": True, "status": "queued", "position": pos}
                _queue_emit = (ctx, pos)

        # Lock is released here — now safe to emit SSE.
        if _queue_emit is not None:
            _ctx_emit, _pos_emit = _queue_emit
            _ctx_emit.emit_status(f"Queued — position {_pos_emit}", "queued")
        return _start_result

    def stop_agent(self, user_id: int):
        """Signal agent to stop. Stop wins over any other action."""
        ctx = get_or_create_context(user_id)
        with ctx.agent_ctrl_lock:
            ctx.agent_ctrl["action"] = "stop"

    def pause_agent(self, user_id: int):
        # Only meaningful if an agent is actually running. Setting
        # action=pause on a queued/idle user used to cause the next queued
        # agent to start and immediately block on check_pause(), leaving
        # users stuck in a silent pause they never asked for.
        if not self.is_running(user_id):
            return
        ctx = get_or_create_context(user_id)
        with ctx.agent_ctrl_lock:
            # Don't clobber an in-flight stop request.
            if ctx.agent_ctrl.get("action") == "stop":
                return
            ctx.agent_ctrl["action"] = "pause"
        ctx.emit_log("Agent paused by user", "warn")
        ctx.emit_status("Paused", "paused")

    def resume_agent(self, user_id: int):
        ctx = get_or_create_context(user_id)
        with ctx.agent_ctrl_lock:
            # Resume only clears a pause. If stop is in-flight we leave it
            # alone so a fast Stop→Resume click doesn't lose the stop intent.
            if ctx.agent_ctrl.get("action") == "pause":
                ctx.agent_ctrl["action"] = None
            elif ctx.agent_ctrl.get("action") == "stop":
                # Stop wins; ignore resume.
                return
            else:
                # Nothing to resume — leave state untouched.
                return
        ctx.emit_log("Agent resumed", "ok")
        ctx.emit_status("Resuming...", "running")

    def _run_agent_thread(self, user_id: int):
        """Run agent in background thread.

        Stability fix (multi-agent bug #7 + CLAUDE.md rule #8): use ONE
        asyncio event loop for the entire thread lifecycle. Previously
        this method opened 5 separate loops (create_run, mark_completed,
        admin_alert, mark_crashed, save_run_log) — each leaks an
        epoll/kqueue fd and over enough runs hits Railway's EMFILE limit.
        """
        ctx = get_or_create_context(user_id)
        run_id = None
        loop = asyncio.new_event_loop()
        # Stability fix (Perplexity bug #49): track the terminal UI
        # status so finally doesn't overwrite a crash with "Idle". The
        # bus only caches the LAST terminal status for SSE replay, so
        # if the except branch emits "error" and finally then emits
        # "idle", a user reconnecting after the crash sees only "Idle"
        # — no clue anything went wrong.
        _final_ui_status = ("Idle — click START to run agent", "idle")
        try:
            # Create run record (uses shared loop)
            try:
                run_id = loop.run_until_complete(db.create_agent_run(user_id))
                ctx.run_id = run_id
            except Exception as _cr_err:
                ctx.emit_log(f"create_agent_run failed: {_cr_err}", "warn")

            # Web-hunt parity with `huntova recipe run`: hydrate
            # HV_RECIPE_ADAPTATION env so the bundled `recipe-adapter`
            # + `adaptation-rules` plugins fire on web hunts too.
            # Round-10 audit Part B — the plugins were CLI-only before.
            # Cleared in finally so future MAX_CONCURRENT_AGENTS>1
            # users don't see each other's adaptation. Best-effort:
            # any failure to load the recipe is logged and ignored.
            _set_adapt_env = False
            _recipe_name = (config or {}).get("recipe_name") if isinstance(config, dict) else None
            if _recipe_name:
                try:
                    _rec = loop.run_until_complete(db.get_hunt_recipe(user_id, _recipe_name))
                    _adapt = (_rec or {}).get("adaptation_json")
                    if isinstance(_adapt, str):
                        import json as _j
                        _adapt = _j.loads(_adapt or "{}")
                    if _adapt:
                        import json as _j2, os as _os
                        _os.environ["HV_RECIPE_ADAPTATION"] = _j2.dumps({
                            "recipe": _recipe_name,
                            "winning_terms": _adapt.get("winning_query_terms") or [],
                            "suppress_terms": _adapt.get("suppress_terms") or [],
                            "added_queries": _adapt.get("recommended_query_additions") or [],
                            "scoring_rules": _adapt.get("scoring_rules") or [],
                        }, ensure_ascii=False, default=str)
                        _set_adapt_env = True
                except Exception as _re:
                    ctx.emit_log(f"recipe adaptation load failed: {_re}", "warn")

            # Import and run the agent
            from app import run_agent_scoped
            try:
                run_agent_scoped(ctx)
            finally:
                if _set_adapt_env:
                    import os as _os
                    _os.environ.pop("HV_RECIPE_ADAPTATION", None)

            # Mark completed (shared loop)
            try:
                if run_id:
                    loop.run_until_complete(db.update_agent_run(
                        run_id, status="completed",
                        leads_found=len(ctx.all_leads),
                        ended_at=datetime.now(timezone.utc).isoformat()
                    ))
            except Exception as _upd_err:
                ctx.emit_log(f"mark_completed failed: {_upd_err}", "warn")

        except Exception as e:
            tb = traceback.format_exc()
            ctx.emit_log(f"Agent crashed: {e}", "error")
            ctx.emit_status("Agent error — click START to retry", "error")
            # Carry forward to finally so the cached terminal status
            # stays "error" instead of being overwritten by "idle".
            _final_ui_status = ("Agent error — click START to retry", "error")
            # Log crash
            try:
                import os
                with open(os.path.join(LOG_DIR, f"crash_{user_id}.log"), "w", encoding="utf-8") as f:
                    f.write(tb)
            except Exception:
                pass
            # Alert admins on crash (shared loop)
            try:
                from config import ADMIN_EMAILS
                import email_service
                if ADMIN_EMAILS:
                    _crash_subj = f"[Huntova] Agent crash — user {user_id}"
                    _crash_body = f"Agent crashed for user {user_id}.\n\nError: {e}\n\nTraceback:\n{tb[:2000]}"
                    for _admin in ADMIN_EMAILS[:3]:
                        try:
                            loop.run_until_complete(email_service.send_email(_admin, _crash_subj, f"<pre>{_crash_body}</pre>", _crash_body))
                        except Exception:
                            pass
            except Exception:
                pass
            # Update run record (shared loop)
            try:
                if run_id:
                    loop.run_until_complete(db.update_agent_run(
                        run_id, status="crashed",
                        ended_at=datetime.now(timezone.utc).isoformat()
                    ))
            except Exception:
                pass
        finally:
            ctx.agent_running = False
            with self._lock:
                self._running.pop(user_id, None)
            ctx.emit_status(*_final_ui_status)
            # Save run log to database (shared loop)
            try:
                _run_log = getattr(ctx, '_run_log', [])
                if _run_log:
                    _log_text = "\n".join(_run_log)
                    _leads_n = len(ctx.all_leads) if ctx.all_leads else 0
                    loop.run_until_complete(db.save_agent_run_log(
                        user_id, run_id or 0, _log_text,
                        leads_found=_leads_n))
                    print(f"[AGENT] Run log saved for user {user_id}: {len(_run_log)} entries, {_leads_n} leads")
            except Exception as _log_err:
                print(f"[AGENT] Failed to save run log: {_log_err}")
            # Close the shared loop exactly once
            try:
                loop.close()
            except Exception:
                pass
            # Close cached session log file handle (bug #10 — was opened on
            # every emit_log call, now held open per run, so it must be
            # explicitly released here). Never raises.
            try:
                ctx.close_session_log()
            except Exception:
                pass
            # Clean up heavy context data to free memory (keep bus for SSE)
            ctx.seen_urls = set()
            ctx.seen_fps = set()
            ctx.domain_fails = {}
            ctx.all_leads = []
            ctx.found_domains = []
            ctx._cached_dna = None
            ctx._user_settings = None
            ctx._run_log = []
            # Process queue
            self._process_queue()

    def _process_queue(self):
        """Start next queued agent if capacity available.

        Stability fix (Perplexity bug #65): mirror of #64 in start_agent.
        If a user clicked Stop while queued, ctx.agent_ctrl["action"]
        is "stop". Previously this function wholesale-reset that ctrl
        and spawned the thread anyway, ignoring the user's stop. Now
        we honour the stop intent: clear it, skip the spawn, and emit
        a cancellation status.
        """
        started = []  # list of (kind, ctx) where kind in ("started", "stopped")
        remaining = []
        with self._lock:
            while self._queue and len(self._running) < MAX_CONCURRENT_AGENTS:
                next_user_id = self._queue.popleft()
                ctx = get_or_create_context(next_user_id)
                _existing_ctrl = getattr(ctx, "agent_ctrl", None)
                with ctx.agent_ctrl_lock:
                    if isinstance(_existing_ctrl, dict) and _existing_ctrl.get("action") == "stop":
                        # User cancelled while queued — clear the flag
                        # and skip spawning.
                        _existing_ctrl["action"] = None
                        ctx.agent_running = False
                        started.append(("stopped", ctx))
                        continue
                    if isinstance(_existing_ctrl, dict):
                        _existing_ctrl["action"] = None
                    else:
                        ctx.agent_ctrl = {"action": None}
                    ctx.agent_running = True
                thread = threading.Thread(target=self._run_agent_thread, args=(next_user_id,), daemon=True)
                self._running[next_user_id] = thread
                thread.start()
                started.append(("started", ctx))
            remaining = [(i, uid) for i, uid in enumerate(self._queue)]

        # Emit notifications OUTSIDE the lock to prevent deadlock
        for kind, ctx in started:
            if kind == "started":
                ctx.emit_log("Your turn! Agent starting...", "ok")
                ctx.emit_status("Starting...", "running")
            else:
                ctx.emit_log("Queued run cancelled before start", "warn")
                ctx.emit_status("Stopped", "stopped")
        for i, uid in remaining:
            ctx = get_or_create_context(uid)
            ctx.emit_status(f"Queued — position {i + 1}", "queued")


# Global singleton
agent_runner = AgentRunner()
