"""huntova inbox — IMAP reply detection.

Polls the user's IMAP mailbox and matches incoming messages to outbound
sends Huntova made via `huntova outreach send`. When a reply is detected,
the matching lead's `email_status` flips to `replied`, an entry is
appended to `status_history`, and the agent's adaptive-learning loop
treats it as positive feedback on whatever opener landed.

Wiring (from `cli.py`):
    from cli_inbox import register
    register(subparsers)

Subcommands:
    huntova inbox setup      # save IMAP creds to OS keychain
    huntova inbox check      # one-shot poll, print N replies found
    huntova inbox watch      # daemon loop, polls every --interval seconds

State on disk: nothing extra. Outbound message_id sits on the lead's
JSON `data` blob under `_message_id` and `_sent_at`. Reply matching
uses the recipient's `In-Reply-To` / `References` headers + a fallback
`Subject:` heuristic for clients that strip thread headers.
"""

from __future__ import annotations

import argparse
import asyncio
import imaplib
import os
import re
import ssl
import sys
import time
from datetime import datetime, timezone
from email import message_from_bytes
from email.utils import getaddresses, parseaddr


def _bold(s: str) -> str: return f"\033[1m{s}\033[0m"
def _dim(s: str) -> str: return f"\033[2m{s}\033[0m"
def _green(s: str) -> str: return f"\033[32m{s}\033[0m"
def _red(s: str) -> str: return f"\033[31m{s}\033[0m"
def _yellow(s: str) -> str: return f"\033[33m{s}\033[0m"


# ── credentials helpers ─────────────────────────────────────────────

_KEY_HOST = "HV_IMAP_HOST"
_KEY_PORT = "HV_IMAP_PORT"
_KEY_USER = "HV_IMAP_USER"
_KEY_PASS = "HV_IMAP_PASSWORD"


def _load_imap_settings() -> dict | None:
    """Resolve IMAP creds from env first, then OS keychain. None if
    nothing is configured — callers print a friendly setup hint."""
    host = os.environ.get(_KEY_HOST)
    user = os.environ.get(_KEY_USER)
    port = os.environ.get(_KEY_PORT)
    password = os.environ.get(_KEY_PASS)
    if not (host and user and password):
        try:
            from secrets_store import get_secret
            host = host or get_secret(_KEY_HOST)
            user = user or get_secret(_KEY_USER)
            port = port or get_secret(_KEY_PORT)
            password = password or get_secret(_KEY_PASS)
        except Exception:
            pass
    if not (host and user and password):
        return None
    return {
        "host": host,
        "port": int(port or 993),
        "user": user,
        "password": password,
    }


# ── IMAP connection ─────────────────────────────────────────────────

def _connect(s: dict) -> imaplib.IMAP4_SSL:
    ctx = ssl.create_default_context()
    cli = imaplib.IMAP4_SSL(s["host"], s["port"], ssl_context=ctx, timeout=20)
    cli.login(s["user"], s["password"])
    return cli


# ── thread-header parsing ───────────────────────────────────────────

_MID_RE = re.compile(r"<([^>]+)>")


def _split_msgids(value: str) -> list[str]:
    """Pull every `<id>` from an `In-Reply-To` or `References` header.
    Both can hold multiple IDs; clients delimit by whitespace."""
    if not value:
        return []
    return [m.group(1).strip() for m in _MID_RE.finditer(value)]


# ── outbound index ──────────────────────────────────────────────────

async def _build_outbound_index(user_id: int) -> tuple[dict, dict]:
    """Walk the user's leads once, return:
    - {message_id: lead_id} for direct In-Reply-To matching
    - {sender_email_lower: lead_id} as a from-address fallback for
      clients that strip threading headers (a few mobile clients still
      do; "Reply" lands without In-Reply-To).
    """
    import db as _db
    leads = await _db.get_leads(user_id, limit=2000)
    by_msgid: dict[str, str] = {}
    by_email: dict[str, str] = {}
    for ld in leads or []:
        lid = ld.get("lead_id")
        mid = (ld.get("_message_id") or "").strip()
        em = (ld.get("contact_email") or "").strip().lower()
        if mid and lid:
            by_msgid[mid.lstrip("<").rstrip(">")] = lid
        if em and lid and em not in by_email:
            by_email[em] = lid
    return by_msgid, by_email


# ── reply matcher ───────────────────────────────────────────────────

def _is_autoreply(subject: str, headers: list[tuple[str, str]]) -> bool:
    """RFC 3834 + common heuristic. We do NOT mark these as 'replied';
    they're noise."""
    s_lower = (subject or "").lower()
    if any(s_lower.startswith(p) for p in ("auto:", "automatic reply", "out of office",
                                            "out-of-office", "vacation:")):
        return True
    h = {k.lower(): v.lower() for k, v in headers}
    if h.get("auto-submitted") and h["auto-submitted"] != "no":
        return True
    if h.get("x-autoreply") or h.get("x-auto-response-suppress"):
        return True
    return False


async def _scan_inbox(user_id: int, since_days: int = 14, dry_run: bool = False) -> dict:
    """Connect, fetch unseen messages from the last N days, match each
    to the outbound index, and update lead.email_status='replied' for
    matches. Returns a summary dict."""
    s = _load_imap_settings()
    if not s:
        return {"ok": False, "error": "IMAP not configured. Run `huntova inbox setup`."}

    by_msgid, by_email = await _build_outbound_index(user_id)
    if not by_msgid and not by_email:
        return {"ok": True, "matched": 0, "scanned": 0,
                "note": "No outbound messages tracked yet — send some first."}

    import db as _db
    matched, scanned, autoreplied = 0, 0, 0
    since = (datetime.now(timezone.utc) - _td(days=since_days)).strftime("%d-%b-%Y")

    cli = _connect(s)
    try:
        cli.select("INBOX")
        # UNSEEN limits noise; SINCE bounds it. IMAP uses dd-Mon-yyyy.
        typ, data = cli.search(None, f'(SINCE "{since}")')
        ids = (data[0] or b"").split() if typ == "OK" else []
        scanned = len(ids)
        for raw_id in ids:
            try:
                typ, fetched = cli.fetch(raw_id, "(RFC822)")
            except imaplib.IMAP4.error:
                continue
            if typ != "OK" or not fetched or not fetched[0]:
                continue
            try:
                msg = message_from_bytes(fetched[0][1])
            except Exception:
                continue
            subj = (msg.get("Subject") or "").strip()
            if _is_autoreply(subj, list(msg.items())):
                autoreplied += 1
                continue
            in_reply = msg.get("In-Reply-To") or ""
            references = msg.get("References") or ""
            from_addr = (parseaddr(msg.get("From") or "")[1] or "").strip().lower()

            # 1) Direct match via In-Reply-To / References
            lid = None
            for cand in _split_msgids(in_reply) + _split_msgids(references):
                if cand in by_msgid:
                    lid = by_msgid[cand]
                    break
            # 2) From-address fallback when the client stripped headers
            if not lid and from_addr in by_email:
                lid = by_email[from_addr]
            if not lid:
                continue

            matched += 1
            if dry_run:
                continue

            now_iso = datetime.now(timezone.utc).isoformat()

            def _mut(lead: dict, _now: str = now_iso, _from: str = from_addr,
                     _subj: str = subj) -> dict:
                if lead.get("email_status") in ("replied", "won", "meeting_booked"):
                    return lead
                lead["email_status"] = "replied"
                lead["email_status_date"] = _now
                lead["_reply_subject"] = _subj[:200]
                lead["_reply_from"] = _from[:200]
                lead["_reply_date"] = _now
                # Auto-pause the follow-up sequence — no point sending
                # the Day +4 bump to someone who already replied.
                lead["_seq_paused"] = True
                h = lead.get("status_history", [])
                if not h or h[-1].get("status") != "replied":
                    h.append({"status": "replied", "date": _now})
                    if len(h) > 100:
                        h = h[-100:]
                lead["status_history"] = h
                return lead

            try:
                await _db.merge_lead(user_id, lid, _mut)
                # Treat the reply as a 'good' feedback signal so the
                # next DNA refinement learns from it.
                try:
                    await _db.save_lead_feedback(user_id, lid, "good", "imap_reply")
                except Exception:
                    pass
            except Exception as e:
                print(f"  {_red('!')} merge_lead failed for {lid}: {e}", file=sys.stderr)
    finally:
        try:
            cli.close()
        except Exception:
            pass
        try:
            cli.logout()
        except Exception:
            pass

    return {"ok": True, "scanned": scanned, "matched": matched,
            "autoreplied": autoreplied, "dry_run": dry_run}


def _td(days: int):
    from datetime import timedelta
    return timedelta(days=days)


# ── subcommands ─────────────────────────────────────────────────────

def _cmd_setup(args: argparse.Namespace) -> int:
    print(_bold("Huntova IMAP setup"))
    print(_dim("Stored in your OS keychain (or fallback) — same place your AI key lives.\n"))
    host = (args.host or "").strip() or input("IMAP host (e.g. imap.gmail.com): ").strip()
    if not host:
        print(_red("aborted: host required"), file=sys.stderr); return 1
    port = (args.port or "").strip() or input("Port [993]: ").strip() or "993"
    user = (args.user or "").strip() or input("Username (full email): ").strip()
    if not user:
        print(_red("aborted: user required"), file=sys.stderr); return 1
    if args.password:
        password = args.password
    else:
        from getpass import getpass
        password = getpass("Password / app-password: ").strip()
    if not password:
        print(_red("aborted: password required"), file=sys.stderr); return 1
    try:
        from secrets_store import set_secret
        set_secret(_KEY_HOST, host)
        set_secret(_KEY_PORT, str(port))
        set_secret(_KEY_USER, user)
        set_secret(_KEY_PASS, password)
    except Exception as e:
        print(_red(f"keychain write failed: {e}"), file=sys.stderr); return 1
    print(f"{_green('✓')} saved. Run `huntova inbox check` to test.")
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    from cli import _bootstrap_local_env  # late import — circular otherwise
    user_id = _bootstrap_local_env()
    if user_id is None:
        return 1
    res = asyncio.run(_scan_inbox(user_id, since_days=int(args.since), dry_run=bool(args.dry_run)))
    if not res.get("ok"):
        print(_red(res.get("error", "scan failed")), file=sys.stderr); return 1
    print(f"  {_dim('scanned')} {res.get('scanned', 0)}")
    print(f"  {_green('replies matched')} {res.get('matched', 0)}")
    if res.get("autoreplied"):
        print(f"  {_yellow('auto-replies skipped')} {res.get('autoreplied', 0)}")
    if res.get("note"):
        print(f"  {_dim(res['note'])}")
    return 0


def _cmd_watch(args: argparse.Namespace) -> int:
    from cli import _bootstrap_local_env
    user_id = _bootstrap_local_env()
    if user_id is None:
        return 1
    interval = max(60, int(args.interval))
    print(f"{_bold('huntova inbox watch')} — polling every {interval}s. Ctrl-C to stop.")
    while True:
        try:
            res = asyncio.run(_scan_inbox(user_id, since_days=int(args.since)))
            ts = datetime.now().strftime("%H:%M:%S")
            if not res.get("ok"):
                print(f"  {ts} {_red('!')} {res.get('error')}", file=sys.stderr)
            else:
                m = res.get("matched", 0)
                if m:
                    print(f"  {ts} {_green('✓')} {m} new "
                          f"{'reply' if m == 1 else 'replies'}")
                elif args.verbose:
                    _scanned = res.get("scanned", 0)
                    print(f"  {ts} {_dim('scanned ' + str(_scanned) + ', no matches')}")
        except KeyboardInterrupt:
            print(f"\n{_dim('stopped')}"); return 0
        except Exception as e:
            print(f"  {_red('!')} poll error: {type(e).__name__}: {e}", file=sys.stderr)
        time.sleep(interval)


# ── argparse wiring ─────────────────────────────────────────────────

def register(subparsers) -> None:
    """Add `huntova inbox` and its three subcommands."""
    p = subparsers.add_parser(
        "inbox", help="poll IMAP and match replies to your outbound sends",
        description=("Reply detection. Connects to your IMAP mailbox, "
                     "matches incoming messages to outbound sends from "
                     "`huntova outreach send`, flips matching leads to "
                     "email_status=replied + appends to status_history."),
        epilog=("Examples:\n"
                "  huntova inbox setup\n"
                "  huntova inbox check\n"
                "  huntova inbox watch --interval 300\n\n"
                "Docs: https://github.com/enzostrano/huntova-public\n"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="inbox_cmd", required=True)

    p_setup = sub.add_parser("setup", help="save IMAP creds to the OS keychain")
    p_setup.add_argument("--host", default="", help="IMAP host (e.g. imap.gmail.com)")
    p_setup.add_argument("--port", default="", help="IMAP port [993]")
    p_setup.add_argument("--user", default="", help="username (full email)")
    p_setup.add_argument("--password", default="", help="(prefer the prompt — passing this on the CLI leaks via shell history)")
    p_setup.set_defaults(func=_cmd_setup)

    p_check = sub.add_parser("check", help="one-shot poll, print summary")
    p_check.add_argument("--since", default="14", help="look back N days [14]")
    p_check.add_argument("--dry-run", action="store_true", help="match but don't update leads")
    p_check.set_defaults(func=_cmd_check)

    p_watch = sub.add_parser("watch", help="poll loop until Ctrl-C")
    p_watch.add_argument("--interval", default="300", help="seconds between polls [300]; min 60")
    p_watch.add_argument("--since", default="3", help="look back N days each poll [3]")
    p_watch.add_argument("--verbose", action="store_true", help="log even when no matches")
    p_watch.set_defaults(func=_cmd_watch)
