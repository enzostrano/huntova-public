"""Terminal-first surfaces for Huntova (a284).

Three commands that turn the terminal into a first-class operations
console — useful because closing the terminal kills `huntova serve`
anyway, so the terminal might as well do real work.

- `huntova tail` — connect to a running local server and print live
  events (hunt:start, lead:scored, sequence:sent, inbox:reply, ...)
  as they happen. Like `tail -f` for the agent's SSE bus.

- `huntova run` — start the server in a daemon thread and drop into
  an interactive chat REPL in the same terminal. Type a message,
  press Enter, see the AI's response + any actions taken. Auto-starts
  the server when nothing is listening on the chosen port.

The serve command also gains a `--logs` flag: silent uvicorn becomes a
live colored event stream by spawning a tail thread once the server is
up. Same code path as `huntova tail`, just baked into the same process.

No new third-party deps: stdlib only (urllib, json, socket, threading,
argparse). ANSI colors degrade to plain text on non-TTY / NO_COLOR.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request

# ── ANSI colors ────────────────────────────────────────────────────

def _color_enabled() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


def _c(name: str, text: str) -> str:
    if not _color_enabled():
        return text
    codes = {
        "cyan": "36", "yellow": "33", "green": "32", "red": "31",
        "magenta": "35", "blue": "34", "dim": "2", "bold": "1",
    }
    return f"\033[{codes.get(name, '0')}m{text}\033[0m"


# ── Server probing ──────────────────────────────────────────────────

def _server_alive(host: str, port: int, timeout: float = 0.5) -> bool:
    """True if a TCP listener is accepting on host:port. Cheap pre-flight
    so we don't try to open SSE / POST against a dead server."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def _wait_for_server(host: str, port: int, max_wait_s: float = 10.0) -> bool:
    """Poll until a server starts accepting or the budget elapses. Used
    after spawning uvicorn in a daemon thread."""
    deadline = time.monotonic() + max_wait_s
    while time.monotonic() < deadline:
        if _server_alive(host, port, timeout=0.3):
            return True
        time.sleep(0.2)
    return False


# a285: module-level handle so the REPL can request a graceful shutdown
# of the uvicorn server it spawned. Keyed by (host, port) in case some
# future caller starts more than one — only one is expected today.
_managed_servers: dict = {}


def _start_server_thread(host: str, port: int):
    """Start uvicorn in a daemon thread so the parent CLI process owns
    the foreground for the chat REPL. Daemon=True ensures the server
    dies even if the parent exits without us calling shutdown — but
    `_shutdown_managed_server` is the cooperative path that closes
    listening sockets, drains in-flight requests, and runs lifespan
    teardown so DB pools + agent threads close cleanly.

    a285 fix: also hydrate API keys from the local config / keychain
    BEFORE uvicorn starts. cmd_serve does this; cmd_run was missing it,
    so users who saved their key via `huntova onboard` got a "no provider
    configured" error on their first chat message even though the key
    was on disk.
    """
    os.environ.setdefault("APP_MODE", "local")
    try:
        from cli import _hydrate_env_from_local_config  # noqa: WPS433 — runtime hydration
        _hydrate_env_from_local_config()
    except Exception as _e:
        # Hydration is best-effort; the server will still boot, but the
        # first chat may fail with "no provider" if the user hasn't set
        # env vars manually. Print a clear hint instead of silent.
        print(_c("yellow", f"⚠ Couldn't hydrate keychain into env ({_e}). "
                          f"Keys may not be available — try `huntova onboard`."))
    import uvicorn  # delayed import — keeps `huntova tail` snappy when
                    # uvicorn isn't strictly required.
    cfg = uvicorn.Config(
        "server:app", host=host, port=port,
        log_level="warning", access_log=False,
    )
    srv = uvicorn.Server(cfg)
    t = threading.Thread(target=srv.run, daemon=True, name="huntova-uvicorn")
    t.start()
    _managed_servers[(host, port)] = (srv, t)
    return t


def _shutdown_managed_server(host: str, port: int, timeout_s: float = 5.0) -> None:
    """Cooperative shutdown of a uvicorn server we started. Sets
    should_exit=True (signals uvicorn's main loop to bail), then joins
    the thread up to timeout_s before returning. Idempotent + safe to
    call when no server was managed (just returns)."""
    entry = _managed_servers.pop((host, port), None)
    if not entry:
        return
    srv, t = entry
    try:
        srv.should_exit = True
        srv.force_exit = False  # let lifespan teardown run
    except Exception:
        pass
    try:
        t.join(timeout=timeout_s)
    except Exception:
        pass


# ── SSE event tail ──────────────────────────────────────────────────

# Per-event styling. Each event maps to a color + a body formatter so
# the tail looks structured rather than dumping raw JSON.
_EVENT_STYLE = {
    "lead": ("green",
             lambda d: f"{d.get('org_name', '?')} — score {d.get('score', '—')} "
                       f"({(d.get('country') or '?')})"),
    "thought": ("dim",
                lambda d: (d.get("text") or d.get("message") or "")[:200]),
    "progress": ("cyan",
                 lambda d: f"{d.get('phase', '')}: {d.get('message', '')}"),
    "status": ("yellow",
               lambda d: f"agent {d.get('state') or d.get('status', '?')}"),
    "log": ("dim",
            lambda d: (d.get("message") or d.get("text") or "")[:200]),
    "screenshot": ("dim",
                   lambda d: f"screenshot {d.get('url', '')[:80]}"),
    "browsing_state": ("dim",
                       lambda d: f"browsing {d.get('url', '')[:80]}"),
    "crm_refresh": ("cyan", lambda d: "CRM refreshed"),
    "credits_exhausted": ("red", lambda d: "credits exhausted (cloud only)"),
    "research_progress": ("cyan",
                          lambda d: f"research: {d.get('message', '')}"),
    "research_done": ("green",
                      lambda d: f"research done — {d.get('lead_id', '?')}"),
    "scan_report": ("cyan",
                    lambda d: f"scan: {d.get('url', '?')[:80]} "
                              f"({d.get('pages_visited', '?')} pages)"),
    "subagent_status": ("magenta",
                        lambda d: f"subagent#{d.get('id', '?')} "
                                  f"{d.get('kind', '')} → {d.get('status', '?')}"),
    "lead_action": ("cyan",
                    lambda d: f"lead {d.get('lead_id', '?')} → "
                              f"{d.get('action', '?')}"),
    "memory_recorded": ("green",
                        lambda d: f"memory: {(d.get('key') or '')[:40]}"),
}


def _format_label(name: str) -> str:
    color, _ = _EVENT_STYLE.get(name, ("cyan", None))
    return _c(color, f"[{name}]".ljust(20))


def _format_body(name: str, data: dict) -> str:
    style = _EVENT_STYLE.get(name)
    if style:
        try:
            body = style[1](data)
            return body if isinstance(body, str) else str(body)
        except Exception:
            pass
    if not data:
        return ""
    try:
        return json.dumps(data, default=str)[:200]
    except Exception:
        return str(data)[:200]


def _print_event(name: str, raw_data: str) -> None:
    """Render one SSE event as a single colored timestamped line."""
    ts = time.strftime("%H:%M:%S")
    try:
        d = json.loads(raw_data) if raw_data and raw_data.strip().startswith(("{", "[")) else {"raw": raw_data}
    except Exception:
        d = {"raw": raw_data[:200]}
    label = _format_label(name)
    body = _format_body(name, d if isinstance(d, dict) else {"data": d})
    print(f"{_c('dim', ts)} {label} {body}", flush=True)


def _tail_loop(host: str, port: int, on_disconnect=None) -> int:
    """Open SSE and stream events until interrupted. Returns exit code.

    a285 fix: clean stream-end (server closes the half) used to fall
    through with no diagnostic AND with backoff reset to 1.0, making
    the next iteration reconnect immediately and tight-loop the server
    if it kept closing. Now: treat clean stream-end as a disconnect
    and apply the same backoff + server-alive exit path. Also:
    finite connect timeout (15s) so `urlopen(timeout=None)` doesn't
    hang forever on a server that's mid-startup.
    """
    url = f"http://{host}:{port}/agent/events"
    backoff = 1.0
    while True:
        try:
            req = urllib.request.Request(
                url,
                headers={"Accept": "text/event-stream",
                         "User-Agent": "huntova-cli/tail"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                # Reset backoff on successful connection
                backoff = 1.0
                current_event = None
                _data_buf = []  # a285: buffer multi-line data: per SSE spec
                for raw_line in resp:
                    if not raw_line:
                        continue
                    line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                    if line.startswith("event:"):
                        current_event = line[6:].strip()
                    elif line.startswith("data:"):
                        # Per SSE spec: collect data: lines, dispatch on
                        # the blank-line frame terminator. Today's server
                        # emits one data: per event so this is a no-op,
                        # but a future multi-line emit (or embedded \n)
                        # used to render as partial frames.
                        _data_buf.append(line[5:].strip())
                    elif line == "":
                        # End of one event block — dispatch + reset.
                        if _data_buf:
                            payload = "\n".join(_data_buf).strip()
                            if payload and payload != "[]":
                                _print_event(current_event or "message", payload)
                        current_event = None
                        _data_buf = []
                    elif line.startswith(":"):
                        # SSE comment / keep-alive — silent.
                        continue
                # a285 fix: clean stream-end (no exception). Treat as a
                # disconnect rather than tight-looping reconnect.
                print(_c("yellow", f"⚠ Stream ended. Reconnecting in {backoff:.0f}s…"))
                if on_disconnect:
                    try: on_disconnect()
                    except Exception: pass
                time.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
                if not _server_alive(host, port, timeout=1.0):
                    print(_c("red", f"✗ Server at {host}:{port} is gone. Exiting."))
                    return 1
        except KeyboardInterrupt:
            print()
            print(_c("dim", "▾ Tail stopped."))
            return 0
        except urllib.error.URLError as e:
            print(_c("yellow", f"⚠ Disconnected: {e.reason}. Reconnecting in {backoff:.0f}s…"))
            if on_disconnect:
                try: on_disconnect()
                except Exception: pass
            time.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
            if not _server_alive(host, port, timeout=1.0):
                # If the server is gone, exit cleanly instead of looping
                # forever — usually means the user closed `huntova serve`.
                print(_c("red", f"✗ Server at {host}:{port} is gone. Exiting."))
                return 1
        except Exception as e:
            print(_c("red", f"✗ Tail error: {type(e).__name__}: {e}"))
            time.sleep(backoff)
            backoff = min(backoff * 2, 30.0)


def cmd_tail(args: argparse.Namespace) -> int:
    """Tail the local server's SSE event stream."""
    host = getattr(args, "host", None) or "127.0.0.1"
    port = int(getattr(args, "port", None) or 5050)
    if not _server_alive(host, port):
        print(_c("red", f"✗ No Huntova server at {host}:{port}."))
        print(_c("dim", f"  Start one in another terminal with `huntova serve` (or `huntova run`)."))
        return 1
    print(_c("cyan", f"▸ Tailing http://{host}:{port}/agent/events — Ctrl+C to stop"))
    print(_c("dim", "  Events will appear here as Huntova works (hunts, leads, sequences, inbox, ...)"))
    print()
    return _tail_loop(host, port)


# ── Interactive chat REPL ───────────────────────────────────────────

_CHAT_HELP = """\
  /help          this help
  /quit  /q      exit chat (Ctrl+D also works)
  /clear         start a new conversation
  /status        show agent state + quick stats
  /provider X    pin a provider for next message (e.g. /provider anthropic)
  /tail          launch the SSE tail in this terminal (cancels chat)
  Any other line is sent to /api/chat — same as the dashboard's chat.
  Use @<slot> at the start of a message (e.g. @prospector) to address a
  specific specialist from your team panel.
"""


def _chat_post(base_url: str, message: str, conversation_id, provider: str = "") -> dict:
    """POST to /api/chat with a 120s timeout. Returns parsed JSON or
    raises on HTTP / network error."""
    body = {"message": message, "source": "cli"}
    if conversation_id is not None:
        body["conversation_id"] = conversation_id
    if provider:
        body["provider"] = provider
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/api/chat",
        data=data,
        headers={"Content-Type": "application/json",
                 "User-Agent": "huntova-cli/chat"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _print_chat_response(p: dict) -> None:
    """Render an /api/chat reply. Different actions get different
    visual treatments so the user can scan a session quickly."""
    if not isinstance(p, dict):
        print(_c("dim", "  ") + str(p))
        return
    action = (p.get("action") or "").strip()
    text = (p.get("text") or "").strip()
    result = p.get("result")
    if action == "answer" and text:
        print(_c("cyan", "  ◇") + " " + text)
        return
    if action == "done":
        print(_c("green", "  ✓") + " " + (text or "Done."))
        if result:
            try:
                pretty = json.dumps(result, default=str, indent=2)
                for line in pretty.splitlines():
                    print(_c("dim", "    " + line))
            except Exception:
                print(_c("dim", "    " + str(result)[:300]))
        return
    if action and text:
        print(_c("yellow", f"  [{action}]") + " " + text)
        if result:
            try:
                print(_c("dim", "    " + json.dumps(result, default=str)[:400]))
            except Exception:
                print(_c("dim", "    " + str(result)[:400]))
        return
    if text:
        print(_c("cyan", "  ◇") + " " + text)
        return
    # Unknown shape — dump raw.
    try:
        print(_c("dim", "  " + json.dumps(p, default=str)[:300]))
    except Exception:
        print(_c("dim", "  " + str(p)[:300]))


def _print_suggestions(base_url: str) -> None:
    """Pre-fetch the suggestions strip the dashboard shows. Useful so
    the user opens the REPL and sees what to do next without typing."""
    try:
        with urllib.request.urlopen(
                urllib.request.Request(
                    f"{base_url}/api/suggestions",
                    headers={"User-Agent": "huntova-cli/chat"}),
                timeout=5) as resp:
            d = json.loads(resp.read().decode("utf-8"))
        items = (d.get("suggestions") if isinstance(d, dict) else None) or []
        if not items:
            return
        print(_c("dim", "  Suggested next moves:"))
        for it in items[:4]:
            print(_c("cyan", "  ▸ ") + (it.get("prompt") or ""))
            r = it.get("reason")
            if r:
                print(_c("dim", "      " + str(r)[:160]))
        print()
    except Exception:
        # Suggestions are decoration — never block on failure.
        pass


def cmd_run(args: argparse.Namespace) -> int:
    """Boot the server in a daemon thread, then open an interactive chat.

    `huntova run` is the one-command experience: opens a terminal
    operations console with full access to chat, hunts, sequences, the
    team panel via @<slot> mentions, and persistent memory. The server
    starts in the same process so closing the terminal still shuts
    everything down — but at least the terminal was useful while open.
    """
    host = getattr(args, "host", None) or "127.0.0.1"
    port = int(getattr(args, "port", None) or 5050)
    base_url = f"http://{host}:{port}"

    started_here = False
    if not _server_alive(host, port):
        print(_c("yellow", f"▸ Starting Huntova on {host}:{port}…"))
        _start_server_thread(host, port)
        if not _wait_for_server(host, port, max_wait_s=12.0):
            print(_c("red", f"✗ Server didn't come up on {host}:{port}."))
            print(_c("dim", "  Check the keychain has an API key (`huntova onboard`)."))
            return 1
        started_here = True
        print(_c("green", f"✓ Server up at {base_url}"))
    else:
        print(_c("dim", f"▸ Using existing server at {base_url}"))

    print(_c("cyan", "─" * 60))
    print("  " + _c("bold", "Huntova interactive console"))
    print(_c("dim", "  Type a message and press Enter. /help for commands."))
    if started_here:
        print(_c("dim", "  Closing this terminal will stop the server."))
    print(_c("cyan", "─" * 60))
    print()
    _print_suggestions(base_url)

    convo_id = None
    pinned_provider = ""
    def _bye():
        # a285: cooperative shutdown if we started the server. Lets DB
        # pools + agent threads close cleanly instead of getting force-
        # killed at process exit.
        if started_here:
            print(_c("dim", "  ▾ Shutting down server…"))
            _shutdown_managed_server(host, port, timeout_s=5.0)

    while True:
        try:
            line = input(_c("cyan", "› "))
        except EOFError:
            print()
            print(_c("dim", "▾ Console ended."))
            _bye()
            return 0
        except KeyboardInterrupt:
            print()
            print(_c("dim", "▾ Console ended."))
            _bye()
            return 0
        line = line.strip()
        if not line:
            continue
        if line.startswith("/"):
            cmd = line.split()[0].lower()
            if cmd in ("/quit", "/exit", "/q"):
                _bye()
                return 0
            if cmd == "/help":
                print(_CHAT_HELP)
                continue
            if cmd == "/clear":
                convo_id = None
                print(_c("dim", "  ▾ New conversation."))
                continue
            if cmd == "/status":
                try:
                    with urllib.request.urlopen(f"{base_url}/api/status", timeout=5) as resp:
                        st = json.loads(resp.read().decode("utf-8"))
                    print(_c("dim", "  " + json.dumps(st, default=str, indent=2)[:600]))
                except Exception as e:
                    print(_c("red", f"  ✗ /status failed: {e}"))
                continue
            if cmd == "/tail":
                print(_c("dim", "  ▸ Switching to tail mode. Ctrl+C to exit tail."))
                return _tail_loop(host, port)
            if cmd == "/provider":
                parts = line.split(maxsplit=1)
                if len(parts) < 2:
                    print(_c("dim", f"  Current pinned provider: {pinned_provider or '(auto)'}"))
                else:
                    pinned_provider = parts[1].strip().lower()
                    print(_c("dim", f"  ▾ Provider pinned: {pinned_provider or '(auto)'}"))
                continue
            print(_c("yellow", f"  unknown command: {line}. /help for list."))
            continue
        # Send to /api/chat
        try:
            payload = _chat_post(base_url, line, convo_id, provider=pinned_provider)
            convo_id = payload.get("conversation_id") or convo_id
            _print_chat_response(payload)
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read().decode("utf-8"))
                err_msg = body.get("error") if isinstance(body, dict) else None
            except Exception:
                err_msg = None
            print(_c("red", f"  ✗ HTTP {e.code}: {err_msg or e.reason}"))
        except urllib.error.URLError as e:
            print(_c("red", f"  ✗ Network error: {e.reason}"))
            if not _server_alive(host, port, timeout=0.5):
                print(_c("red", "  ✗ Server is no longer responding. Exiting."))
                return 1
        except Exception as e:
            print(_c("red", f"  ✗ {type(e).__name__}: {e}"))


# ── Bridge for `huntova serve --logs` ──────────────────────────────

def attach_log_tail(host: str, port: int) -> threading.Thread:
    """Spawn a background thread that connects to the SSE bus and prints
    events to stdout. Called from cmd_serve when --logs is set; the
    server runs in the foreground (cmd_serve's uvicorn.run) and the
    tail thread streams events from it.

    Daemon=True so when uvicorn exits (Ctrl+C) the tail thread dies too.
    """
    def _run():
        if not _wait_for_server(host, port, max_wait_s=15.0):
            print(_c("red", f"✗ Log tail couldn't reach server at {host}:{port}"))
            return
        print(_c("cyan", "▸ Live log tail attached — events will print as they happen."))
        try:
            _tail_loop(host, port)
        except Exception as e:
            print(_c("red", f"✗ Log tail crashed: {e}"))

    t = threading.Thread(target=_run, daemon=True, name="huntova-log-tail")
    t.start()
    return t
