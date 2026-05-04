"""huntova remote — control Huntova from your phone via a chat app.

Huntova runs locally on your machine. This module ships a Telegram-bot
bridge so you can peek + drive without sitting at the desktop: long-
poll the Telegram API, route inbound text to the local /api/chat
dispatcher, send responses back. Same brain the dashboard chat uses —
phone in your pocket, agent at your desk.

Subcommands:
    huntova remote setup    → interactive: bot token + chat-id whitelist
    huntova remote test     → ping Telegram, send a hello
    huntova remote start    → foreground long-poll loop (Ctrl-C to stop)
    huntova remote status   → print config + last-seen update
    huntova remote stop     → kill a running `remote start` (PID file)
    huntova remote notify   → send a one-shot message (used by the
                              agent-runner hook for "hunt complete")

Why Telegram first? Cleanest bot API of the messaging trio:
- No request signing (Slack), no Discord-style heartbeat
- Long-poll over HTTPS — works behind any NAT, no inbound port
- Bot tokens revocable from a phone in 5s if leaked

Slack / Discord can land in later releases reusing the same dispatch
shape (parse → /api/chat → reply via provider-specific send).

Token storage: secrets_store (keychain). Whitelist + last-update
cursor: ~/.config/huntova/remote.json. No DB schema changes.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path

# Re-export module so cli.py can register subcommands lazily.
__all__ = ["register", "send_notification"]


# ── paths + config ─────────────────────────────────────────────────

def _config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    p = Path(base) / "huntova"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _config_path() -> Path:
    return _config_dir() / "remote.json"


def _pid_path() -> Path:
    return _config_dir() / "remote.pid"


def _load_config() -> dict:
    p = _config_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text("utf-8")) or {}
    except Exception:
        return {}


def _save_config(cfg: dict) -> None:
    # a426 fix (BRAIN-65): fsync before atomic-rename. Same durability
    # gap as BRAIN-52 (_atomic_write in app.py) — POSIX atomic-rename
    # only guarantees the directory entry is atomic, NOT that the
    # inode's data blocks are persisted. Power loss between rename
    # and disk-flush left remote.json pointing to an unwritten
    # inode → empty config on restart, allowlist gone, and per
    # BRAIN-61 the bot now refuses to start. Better than silent
    # corruption but still a recoverable-state bug — this fix turns
    # the post-crash state into "config still intact" instead.
    p = _config_path()
    tmp = p.with_suffix(".tmp")
    # Open + fsync the file descriptor explicitly (Path.write_text
    # doesn't expose the fd for fsync).
    _data = json.dumps(cfg, indent=2).encode("utf-8")
    with open(tmp, "wb") as f:
        f.write(_data)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    # 0600 — bot tokens aren't here (keychain) but chat-id whitelist
    # is still PII the user expects to be private to their account.
    try:
        os.chmod(tmp, 0o600)
    except Exception:
        pass
    tmp.replace(p)


_TOKEN_SECRET_KEY = "hv_telegram_bot_token"


def _get_token() -> str | None:
    try:
        from secrets_store import get_secret
        return get_secret(_TOKEN_SECRET_KEY)
    except Exception:
        return os.environ.get("HV_TELEGRAM_BOT_TOKEN") or None


def _set_token(token: str) -> None:
    from secrets_store import set_secret
    set_secret(_TOKEN_SECRET_KEY, token)


# ── telegram api thin client ───────────────────────────────────────

_TG_BASE = "https://api.telegram.org"


def _tg(method: str, token: str, *, timeout: int = 10, **params) -> dict:
    """Call a Telegram bot method. Returns parsed JSON or {} on error."""
    import requests
    url = f"{_TG_BASE}/bot{token}/{method}"
    try:
        r = requests.post(url, json=params, timeout=timeout)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    try:
        return r.json()
    except Exception:
        return {"ok": False, "error": f"non-JSON response: {r.status_code}"}


# ── inbound dispatcher: telegram → /api/chat ───────────────────────

def _local_server_url() -> str:
    # Default port matches cli.py DEFAULT_PORT. Override via env so
    # users running on a non-default port can still bridge.
    port = os.environ.get("HV_PORT") or "5050"
    host = os.environ.get("HV_HOST") or "127.0.0.1"
    return f"http://{host}:{port}"


def _dispatch_to_chat(text: str) -> dict:
    """POST to /api/chat. Returns the parsed action dict.

    a278: hard-cap inbound text at 4000 chars + reject the empty case.
    The pre-a278 shape forwarded `text` verbatim to /api/chat with no
    length cap, no prompt-injection guard. Even with a276's
    `--accept-any-chat` second-factor protecting --open mode, a
    whitelisted (potentially-shared) chat could send a 50KB payload
    designed to override the system prompt. The 4000-char cap costs
    nothing for legitimate usage (typical phone-typed chat <500 chars)
    and bounds the abuse vector.

    Future hardening (queued): prepend a system constraint message
    that tells the AI "this input came over the Telegram bridge —
    refuse settings/lead mutations" so even a whitelisted attacker
    can't trigger destructive actions. Tracked separately because it
    needs a /api/chat path change.
    """
    text = (text or "").strip()
    if not text:
        return {"action": "answer", "text": "(empty message — nothing to do)"}
    if len(text) > 4000:
        return {"action": "answer",
                "text": f"(message too long — {len(text)} chars, cap is 4000)"}
    import requests
    url = _local_server_url() + "/api/chat"
    try:
        # a279: tag the request with `source: "telegram"` so the
        # dispatcher knows this came over a low-trust bridge and
        # blocks destructive/settings-mutating actions. The dashboard
        # doesn't set source (defaults to "web") so its action access
        # is unchanged.
        r = requests.post(url, json={"message": text, "source": "telegram"}, timeout=120)
    except Exception as e:
        # a278: redact any leaked bot-token from the error message before
        # surfacing it. The Telegram API URL contains `/bot{TOKEN}/`; a
        # `requests` error including the URL would print the token in
        # logs / chat replies. Conservative scrub.
        _err = str(e)
        import re as _re
        _err = _re.sub(r"/bot[A-Za-z0-9:_-]{20,}/", "/bot<redacted>/", _err)
        return {"action": "answer",
                "text": f"(huntova server unreachable at {url}: {_err[:200]})"}
    if r.status_code != 200:
        return {"action": "answer",
                "text": f"(server error {r.status_code})"}
    try:
        return r.json() or {}
    except Exception:
        return {"action": "answer", "text": "(non-JSON server reply)"}


def _format_reply(d: dict) -> str:
    """Render a /api/chat result as a plain-text reply for messaging apps."""
    if not d:
        return "(empty reply)"
    text = d.get("text") or ""
    action = d.get("action") or ""
    # Server-executed actions return {action:done, text, result}; the
    # text already reads cleanly. Client-dispatched actions like
    # start_hunt / list_leads need a hint that the dashboard would
    # have run them — surface that explicitly so the phone user knows.
    if action in ("start_hunt", "list_leads", "navigate"):
        if action == "start_hunt":
            countries = ", ".join(d.get("countries") or []) or "default"
            mx = d.get("max_leads") or "?"
            return (f"Hunt requested ({countries}, max {mx}). "
                    f"Open the dashboard or send `huntova hunt` from "
                    f"the desk to actually start.")
        if action == "list_leads":
            f = d.get("filter") or ""
            return f"Filter: {f or '(none)'}. Open the dashboard to see results."
        if action == "navigate":
            return f"(would navigate to {d.get('page')})"
    return text or json.dumps(d)[:1500]


# ── long-poll loop ─────────────────────────────────────────────────

def _watch_loop(token: str, allowed: set[int], *, verbose: bool = False) -> int:
    # a422 SECURITY FIX (BRAIN-61): refuse to start with an empty
    # allowlist. Pre-fix the dispatch loop guarded with
    # `if allowed and int(chat) not in allowed:` — when `allowed`
    # was empty, the condition short-circuited to False and EVERY
    # incoming Telegram message reached _dispatch_to_chat(). Anyone
    # who knew the bot's @handle (handles are publicly searchable)
    # could send commands like "list leads" / "delete X" / "share
    # top 10" and Huntova would dispatch them against the operator's
    # local install. Per Telegram bot security guidance + Huntova engineering
    # engineering review (engineering review): authorization
    # MUST be on the immutable Telegram user_id / chat_id, fail-closed
    # by default. Now: explicit refusal at startup if no chats are
    # whitelisted, with a clear pointer to `huntova remote setup`.
    if not allowed:
        sys.stderr.write(
            "[remote] REFUSING TO START: no chats whitelisted.\n"
            "[remote] Anyone who knows your bot @handle would otherwise be\n"
            "[remote] able to send commands. Add your Telegram chat ID via\n"
            "[remote]   huntova remote setup\n"
            "[remote] (Telegram chat IDs are immutable numeric IDs — message\n"
            "[remote] @userinfobot if you don't know yours.)\n"
        )
        return 4
    cfg = _load_config()
    offset = int(cfg.get("offset") or 0)
    me = _tg("getMe", token, timeout=10)
    if not me.get("ok"):
        sys.stderr.write(f"[remote] getMe failed: {me.get('description') or me.get('error')}\n")
        return 2
    bot = (me.get("result") or {}).get("username", "?")
    sys.stderr.write(f"[remote] connected as @{bot}, polling Telegram…\n")
    sys.stderr.write(f"[remote] whitelist: {sorted(allowed)}\n")

    # Write PID file so `remote stop` can find us. SIGTERM → graceful exit.
    _pid_path().write_text(str(os.getpid()), encoding="utf-8")
    _stop = {"flag": False}

    def _on_term(*_):
        _stop["flag"] = True
        sys.stderr.write("\n[remote] shutting down…\n")
    try:
        signal.signal(signal.SIGTERM, _on_term)
        signal.signal(signal.SIGINT, _on_term)
    except Exception:
        pass

    try:
        while not _stop["flag"]:
            r = _tg_raw_get_updates(token, offset + 1, long_poll_secs=30)
            if not r.get("ok"):
                # 401 means the token is bad; bail. Anything else, sleep
                # briefly so we don't busy-spin a flaky network.
                desc = r.get("description") or r.get("error") or ""
                if "Unauthorized" in desc or "401" in desc:
                    sys.stderr.write(f"[remote] auth failed: {desc} — fix the token with `huntova remote setup`\n")
                    return 3
                if verbose:
                    sys.stderr.write(f"[remote] poll error: {desc}\n")
                time.sleep(2.0)
                continue
            for upd in r.get("result") or []:
                offset = max(offset, int(upd.get("update_id") or 0))
                msg = upd.get("message") or upd.get("edited_message") or {}
                chat = (msg.get("chat") or {}).get("id")
                text = (msg.get("text") or "").strip()
                if not chat or not text:
                    continue
                # a422 (BRAIN-61): fail-closed — explicit set-membership
                # check, no `allowed and ...` short-circuit. If `allowed`
                # is somehow empty here (shouldn't be — startup guard
                # above bails), drop every message anyway.
                if int(chat) not in allowed:
                    if verbose:
                        sys.stderr.write(f"[remote] ignoring chat {chat} (not in whitelist)\n")
                    # Don't even reply — looks like a closed door.
                    continue
                if verbose:
                    sys.stderr.write(f"[remote] {chat} → {text[:80]}\n")
                d = _dispatch_to_chat(text)
                reply = _format_reply(d)
                _tg("sendMessage", token, chat_id=chat, text=reply[:4000],
                    disable_web_page_preview=True)
            # Persist offset so we don't re-process on restart.
            cfg["offset"] = offset
            _save_config(cfg)
    finally:
        try:
            _pid_path().unlink()
        except Exception:
            pass
    return 0


def _tg_raw_get_updates(token: str, offset: int, long_poll_secs: int = 30) -> dict:
    """getUpdates with proper long-poll timing. Kept separate because the
    HTTP-client timeout and the Telegram long-poll timeout are different
    concepts; conflating them in `_tg(method, **params)` would force a
    short-circuit on every reconnect."""
    import requests
    url = f"{_TG_BASE}/bot{token}/getUpdates"
    try:
        # HTTP timeout = long-poll seconds + 5 of slack so the request
        # doesn't bail before Telegram's natural reply window closes.
        r = requests.post(url, json={"offset": offset, "timeout": long_poll_secs},
                          timeout=long_poll_secs + 5)
        return r.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── outbound notifier (used by agent_runner) ───────────────────────

def send_notification(text: str) -> bool:
    """Best-effort outbound message to all whitelisted chats. Used by
    the agent runner to ping "hunt complete: 5 qualified leads" etc.
    Silent + non-blocking — never raises — so a flaky bot doesn't
    break the agent loop.

    Returns True if at least one message was sent successfully.
    """
    try:
        token = _get_token()
        if not token:
            return False
        cfg = _load_config()
        # a425 fix (BRAIN-64): explicit isinstance(list) check before
        # iterating. Pre-fix a hand-edited remote.json with
        # `notify_chats: "12345"` (string instead of list) would
        # iterate as ['1','2','3','4','5'] and each char-as-int
        # succeeds → Huntova would notify Telegram chats 1, 2, 3,
        # 4, 5 (random real users) on every hunt completion. Same
        # fail-OPEN class as BRAIN-61 — ambiguous config types
        # turning into unintended fan-out. Per Huntova engineering senior-
        # engineer audit on send_notification path.
        _raw_chats = cfg.get("notify_chats")
        if not isinstance(_raw_chats, list) or not _raw_chats:
            _raw_chats = cfg.get("allowed_chats")
        if not isinstance(_raw_chats, list) or not _raw_chats:
            return False
        chats = _raw_chats
        # Stability fix (audit wave 28): coerce + filter chat IDs once,
        # before the fanout loop. The previous version did `int(chat_id)`
        # inside the loop, wrapped by the outer try/except — so a single
        # malformed entry (hand-edited remote.json, type drift, stray
        # string) raised mid-loop, the outer except returned False, and
        # earlier successfully-notified chats were discarded from the
        # `sent` counter. Caller (agent_runner) would think the
        # notification failed and might retry, double-notifying the
        # valid chats.
        valid_chats: list[int] = []
        for c in chats:
            # a425 (BRAIN-64): only int / int-like-string types here.
            # Reject anything else outright instead of `int(c)` which
            # would coerce floats / bool / dict-keys silently.
            if isinstance(c, bool):
                continue  # bool subclasses int — explicit reject
            if not isinstance(c, (int, str)):
                continue
            try:
                valid_chats.append(int(c))
            except (TypeError, ValueError):
                continue
        sent = 0
        for chat_id in valid_chats:
            r = _tg("sendMessage", token,
                    chat_id=chat_id, text=text[:4000],
                    disable_web_page_preview=True)
            if r.get("ok"):
                sent += 1
        return sent > 0
    except Exception:
        return False


# ── subcommands ────────────────────────────────────────────────────

def _cmd_setup(args: argparse.Namespace) -> int:
    print("Huntova remote — Telegram bot setup")
    print("─────────────────────────────────────")
    print("1) Open Telegram, message @BotFather, send /newbot")
    print("2) Pick a name + username for the bot")
    print("3) BotFather replies with a token like 123456:ABC-DEF…")
    print("4) Paste it below.\n")
    try:
        token = (input("Bot token: ").strip())
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        return 1
    if not token or ":" not in token:
        print("Bot tokens look like '12345:ABC…'. Try again.")
        return 1
    me = _tg("getMe", token, timeout=10)
    if not me.get("ok"):
        print(f"Telegram rejected that token: {me.get('description') or me.get('error')}")
        return 1
    bot = (me.get("result") or {}).get("username", "?")
    print(f"✓ Connected as @{bot}")
    _set_token(token)
    print(f"✓ Token saved to keychain (key: {_TOKEN_SECRET_KEY})")

    print("\nNow message your bot from your phone — anything, e.g. 'hi'")
    print("Then come back here and press Enter so I can read your chat ID.")
    try:
        input("Press Enter when you've sent a message…")
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        return 1
    upd = _tg("getUpdates", token, timeout=10, offset=0)
    chats: set[int] = set()
    for u in upd.get("result") or []:
        m = u.get("message") or u.get("edited_message") or {}
        c = (m.get("chat") or {}).get("id")
        if c:
            chats.add(int(c))
    if not chats:
        print("No messages found. Send one to your bot first, then re-run setup.")
        return 1
    print(f"✓ Detected chat IDs: {sorted(chats)}")
    cfg = _load_config()
    cfg["allowed_chats"] = sorted(chats)
    cfg["notify_chats"] = sorted(chats)  # default: notify same set
    _save_config(cfg)
    print(f"✓ Saved whitelist to {_config_path()}")
    print("\nReady. Run `huntova remote start` to begin polling.")
    return 0


def _cmd_test(args: argparse.Namespace) -> int:
    token = _get_token()
    if not token:
        print("No token set. Run `huntova remote setup` first.")
        return 1
    me = _tg("getMe", token, timeout=10)
    if not me.get("ok"):
        print(f"Telegram rejected the token: {me.get('description') or me.get('error')}")
        return 1
    bot = (me.get("result") or {}).get("username", "?")
    cfg = _load_config()
    chats = cfg.get("notify_chats") or cfg.get("allowed_chats") or []
    if not chats:
        print(f"✓ Bot @{bot} reachable, but no chats configured. Run `huntova remote setup`.")
        return 0
    n_ok = 0
    for c in chats:
        r = _tg("sendMessage", token, chat_id=int(c),
                text="✓ huntova remote — test message")
        if r.get("ok"):
            n_ok += 1
        else:
            print(f"  chat {c}: {r.get('description') or r.get('error')}")
    print(f"✓ Bot @{bot} — sent test to {n_ok}/{len(chats)} chats.")
    return 0 if n_ok else 1


def _cmd_start(args: argparse.Namespace) -> int:
    token = _get_token()
    if not token:
        print("No token set. Run `huntova remote setup` first.")
        return 1
    cfg = _load_config()
    allowed = set(int(c) for c in (cfg.get("allowed_chats") or []))
    if not allowed and not args.open:
        print("No whitelisted chats. Run `huntova remote setup`, or pass "
              "`--open` to accept messages from any chat (NOT recommended).")
        return 1
    # Stability fix (audit wave 27): --open is supposed to bypass the
    # whitelist for ad-hoc testing or one-off remote sessions. The
    # previous version still passed the loaded `allowed` set to
    # _watch_loop, where `if allowed and int(chat) not in allowed:
    # continue` re-enforced the whitelist — so --open was silently
    # ignored for any user who had previously run `huntova remote
    # setup`. Empty the set explicitly when --open is passed.
    if args.open:
        # a276: explicit second-factor required. Without it we keep the
        # whitelist (which may already be loaded above) so an accidental
        # --open doesn't silently expose the agent.
        if not getattr(args, "accept_any_chat", False):
            print("[remote] --open requires --accept-any-chat to confirm you understand", file=sys.stderr)
            print("[remote] that ANY Telegram user who finds the bot can drive the agent.", file=sys.stderr)
            print("[remote] Aborting. Re-run with --open --accept-any-chat to proceed.", file=sys.stderr)
            return 2
        print("[remote] --open mode: whitelist DISABLED. Any chat_id can drive the agent.", file=sys.stderr)
        allowed = set()
    return _watch_loop(token, allowed, verbose=args.verbose)


def _cmd_status(args: argparse.Namespace) -> int:
    token = _get_token()
    cfg = _load_config()
    print(f"Token configured: {'yes' if token else 'no'}")
    print(f"Whitelisted chats: {cfg.get('allowed_chats') or '(none)'}")
    print(f"Notify chats:      {cfg.get('notify_chats') or '(none)'}")
    print(f"Last update offset: {cfg.get('offset', 0)}")
    pid_p = _pid_path()
    if pid_p.exists():
        try:
            pid = int(pid_p.read_text("utf-8").strip())
            os.kill(pid, 0)
            print(f"Running: yes (PID {pid})")
        except Exception:
            print("Running: no (stale PID file)")
            try: pid_p.unlink()
            except Exception: pass
    else:
        print("Running: no")
    return 0


def _cmd_stop(args: argparse.Namespace) -> int:
    pid_p = _pid_path()
    if not pid_p.exists():
        print("Not running.")
        return 0
    try:
        pid = int(pid_p.read_text("utf-8").strip())
    except Exception:
        try: pid_p.unlink()
        except Exception: pass
        print("Stale PID file removed.")
        return 0
    try:
        os.kill(pid, signal.SIGTERM)
        print(f"Sent SIGTERM to PID {pid}.")
    except ProcessLookupError:
        try: pid_p.unlink()
        except Exception: pass
        print("Process already exited.")
    except Exception as e:
        print(f"Couldn't stop PID {pid}: {e}")
        return 1
    return 0


def _cmd_notify(args: argparse.Namespace) -> int:
    text = (args.text or "").strip() or "huntova: hello from your desk"
    ok = send_notification(text)
    print("✓ sent" if ok else "✗ no chats configured / send failed")
    return 0 if ok else 1


# ── argparse registration ──────────────────────────────────────────

def register(subparsers) -> None:
    p = subparsers.add_parser(
        "remote",
        help="Drive Huntova from your phone via Telegram",
        description=(
            "Telegram bot bridge. Long-polls Telegram, routes messages to /api/chat, "
            "replies in the same thread. Outbound notifications when hunts complete."
        ),
    )
    sp = p.add_subparsers(dest="remote_cmd")

    p_setup = sp.add_parser("setup", help="Interactive: paste bot token + detect chat IDs")
    p_setup.set_defaults(func=_cmd_setup)

    p_test = sp.add_parser("test", help="Send a test message to verify the bot is wired")
    p_test.set_defaults(func=_cmd_test)

    p_start = sp.add_parser("start", help="Foreground long-poll loop (Ctrl-C to stop)")
    p_start.add_argument("--verbose", action="store_true",
                         help="Log every inbound message + dispatch")
    p_start.add_argument("--open", action="store_true",
                         help="Accept messages from any chat (skip whitelist — NOT recommended)")
    # a276: --open used to silently disable the whitelist with no
    # second confirmation. Anyone who guessed/scraped the bot's
    # username could hit /api/chat (the agent dispatcher) on the
    # user's local machine. Now requires --accept-any-chat as a
    # second-factor opt-in. Without it, --open errors out.
    p_start.add_argument("--accept-any-chat", action="store_true",
                         help="Required alongside --open. Confirms you understand "
                              "ANY Telegram user who finds the bot can drive the agent.")
    p_start.set_defaults(func=_cmd_start)

    p_status = sp.add_parser("status", help="Show config + whether the watcher is running")
    p_status.set_defaults(func=_cmd_status)

    p_stop = sp.add_parser("stop", help="SIGTERM a running `remote start`")
    p_stop.set_defaults(func=_cmd_stop)

    p_notify = sp.add_parser("notify", help="Send a one-shot message to whitelisted chats")
    p_notify.add_argument("text", nargs="?", default="",
                          help="Message to send (default: 'huntova: hello from your desk')")
    p_notify.set_defaults(func=_cmd_notify)

    # Bare `huntova remote` defaults to status — least-surprise.
    p.set_defaults(func=_cmd_status, remote_cmd="status")
