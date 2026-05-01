"""huntova sequence — 3-step cold-outreach cadence.

Closes the loop after `huntova outreach send` (which fires Step 1):
- Day +4 → "Step 2" (a short bump that references the original subject).
- Day +9 → "Step 3" (final, soft breakup).

State on the lead row (set by `huntova outreach send` automatically):

    _seq_step       int  — 1 = opener fired, 2 = bump fired, 3 = final fired,
                           0 / missing = never enrolled.
    _seq_last_at    str  — ISO timestamp of the most recent step.
    _seq_paused     bool — set to True by `huntova inbox watch` when a
                           reply is detected (so the cadence stops).

`huntova sequence run` is the worker. Run it daily from cron /
launchd / `huntova daemon`. It only fires steps whose delay has
elapsed; safe to run as often as you like.

Built-in cadence:

    Step 1 (Day 0)  — your existing AI-drafted opener (already sent
                      by `huntova outreach send`).
    Step 2 (Day 4)  — "Hi {name}, did the note below land at a bad
                      time? — {first_line_of_opener}"
    Step 3 (Day 9)  — "Last note from me — happy to drop the thread
                      if now's not it. {booking_url|''}"

The Day-N templates are intentionally short. They reuse the
opener's subject so they thread visibly in the recipient's client
(via `Re:` prefix + same Subject) and reference the contact's first
name when present.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone


def _bold(s: str) -> str: return f"\033[1m{s}\033[0m"
def _dim(s: str) -> str: return f"\033[2m{s}\033[0m"
def _green(s: str) -> str: return f"\033[32m{s}\033[0m"
def _red(s: str) -> str: return f"\033[31m{s}\033[0m"
def _yellow(s: str) -> str: return f"\033[33m{s}\033[0m"


# ── cadence config (built-in, no YAML for v1) ──────────────────────

# (step_number, delay_days_after_previous, template_id)
_CADENCE = [
    (2, 4, "bump"),
    (3, 5, "final"),  # 9 days from start = 4 + 5 from step 2
]

_DEFAULT_BUMP = (
    "Hi {first_name},\n\n"
    "Did the note below land at a bad time? Wanted to make sure it "
    "didn't get lost.\n\n"
    "{recap}\n\n"
    "Worth a quick chat?\n\n"
    "— {sender_name}"
)

_DEFAULT_FINAL = (
    "Hi {first_name},\n\n"
    "Last note from me — happy to drop the thread if now's not it. "
    "If the timing changes later, I'm here.\n\n"
    "{booking_line}"
    "— {sender_name}"
)


# ── helpers ─────────────────────────────────────────────────────────

def _first_name(contact_name: str | None) -> str:
    raw = (contact_name or "").strip().split()
    return raw[0] if raw else "there"


def _recap(opener_body: str) -> str:
    """Pull the first 1–2 sentences out of the opener so the bump
    references what the recipient already saw without forcing them
    to scroll. Hard-cap to ~220 chars."""
    body = (opener_body or "").strip().split("\n\n", 1)[0]
    return body[:220] + ("…" if len(body) > 220 else "")


def _booking_line(booking_url: str | None) -> str:
    if not booking_url:
        return ""
    return f"Booking link if helpful: {booking_url}\n\n"


def _due(_seq_step: int, _seq_last_at: str | None) -> bool:
    """Is the next step due *now*?"""
    if not _seq_last_at:
        return False
    try:
        last = datetime.fromisoformat(_seq_last_at.replace("Z", "+00:00"))
    except Exception:
        return False
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    delta_days = next((d for s, d, _ in _CADENCE if s == _seq_step + 1), None)
    if delta_days is None:
        return False
    return (datetime.now(timezone.utc) - last) >= timedelta(days=delta_days)


def _template_for(next_step: int) -> tuple[str, str]:
    """Return (template_id, template_text) for the given step."""
    for step, _, tid in _CADENCE:
        if step == next_step:
            return tid, (_DEFAULT_BUMP if tid == "bump" else _DEFAULT_FINAL)
    return "", ""


# ── main worker ─────────────────────────────────────────────────────

async def _run_once(user_id: int, dry_run: bool, max_send: int) -> dict:
    """Find every lead whose next sequence step is due, send it, and
    advance the lead's `_seq_step` counter. Returns a summary dict."""
    import db as _db
    leads = await _db.get_leads(user_id, limit=2000)
    s = await _db.get_settings(user_id) or {}
    sender_name = (s.get("from_name") or "").strip() or "the team"
    booking = (s.get("booking_url") or "").strip()

    # Bridge SMTP env vars the way `huntova outreach` does, so this
    # command also works without the user shelling out.
    import os as _os
    if s.get("smtp_host") and not _os.environ.get("SMTP_HOST"):
        _os.environ["SMTP_HOST"] = str(s["smtp_host"])
    if s.get("smtp_user") and not _os.environ.get("SMTP_USER"):
        _os.environ["SMTP_USER"] = str(s["smtp_user"])
    if s.get("smtp_port") and not _os.environ.get("SMTP_PORT"):
        _os.environ["SMTP_PORT"] = str(s["smtp_port"])
    if not _os.environ.get("SMTP_PASSWORD"):
        try:
            from secrets_store import get_secret
            pw = get_secret("HV_SMTP_PASSWORD")
            if pw:
                _os.environ["SMTP_PASSWORD"] = pw
        except Exception:
            pass

    sent, skipped, paused, errored = 0, 0, 0, 0
    for ld in leads or []:
        if sent >= max_send:
            break
        step = int(ld.get("_seq_step") or 0)
        if step <= 0 or step >= 3:
            skipped += 1
            continue
        if ld.get("_seq_paused"):
            paused += 1
            continue
        if ld.get("email_status") in ("replied", "won", "meeting_booked",
                                       "lost", "ignored"):
            paused += 1
            continue
        if not _due(step, ld.get("_seq_last_at") or ld.get("_sent_at")):
            skipped += 1
            continue

        next_step = step + 1
        tid, tmpl = _template_for(next_step)
        if not tmpl:
            skipped += 1
            continue

        to = (ld.get("contact_email") or "").strip()
        if not to:
            skipped += 1
            continue

        body = tmpl.format(
            first_name=_first_name(ld.get("contact_name")),
            recap=_recap(ld.get("email_body", "")),
            booking_line=_booking_line(booking),
            sender_name=sender_name,
        )
        # Thread on the original subject so it shows up in the same
        # conversation in Gmail / Outlook.
        original_subject = (ld.get("email_subject") or "").strip() or "Following up"
        subject = original_subject if original_subject.lower().startswith("re:") else f"Re: {original_subject}"

        org = ld.get("org_name") or "?"
        print(f"  → {tid:5s} {ld.get('lead_id', '?')} {org} <{to}>")
        if dry_run:
            print(f"    {_dim('subject:')} {subject}")
            for line in body.splitlines()[:6]:
                print(f"    {_dim(line)}")
            sent += 1  # count toward dry-run cap so previews are bounded
            continue

        try:
            from email_service import _send_email_sync
            html = ("<pre style='font-family:inherit;white-space:pre-wrap;font-size:14px'>"
                    + body.replace("<", "&lt;").replace(">", "&gt;") + "</pre>")
            msg_id = _send_email_sync(to, subject, html, plain_body=body)
        except Exception as e:
            print(f"    {_red('✗ send failed:')} {type(e).__name__}: {str(e)[:80]}")
            errored += 1
            continue

        now_iso = datetime.now(timezone.utc).isoformat()

        def _stamp(lead: dict, _step: int = next_step, _ts: str = now_iso,
                   _mid: str | None = msg_id, _to: str = to) -> dict:
            lead["_seq_step"] = _step
            lead["_seq_last_at"] = _ts
            if _mid:
                # Overwrite so reply matching tracks the latest hop.
                lead["_message_id"] = (_mid or "").lstrip("<").rstrip(">")
            lead["_sent_at"] = _ts
            lead["_sent_to"] = _to
            return lead

        try:
            await _db.merge_lead(user_id, ld.get("lead_id"), _stamp)
            await _db.save_lead_action(
                user_id, ld.get("lead_id") or "?",
                "email_sent",
                score_band=tid,
                meta=__import__("json").dumps({"to": to, "step": next_step,
                                                "subject": subject[:80]}),
            )
        except Exception as e:
            print(f"    {_red('✗ persist failed:')} {type(e).__name__}: {str(e)[:80]}")

        sent += 1

    return {"ok": True, "sent": sent, "skipped": skipped,
            "paused": paused, "errored": errored, "dry_run": dry_run}


# ── subcommands ─────────────────────────────────────────────────────

def _cmd_run(args: argparse.Namespace) -> int:
    from cli import _bootstrap_local_env
    user_id = _bootstrap_local_env()
    if user_id is None:
        return 1
    smtp_ok = all(__import__("os").environ.get(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"))
    if not smtp_ok and not args.dry_run:
        # Try DB-bridged settings (mirrors cmd_outreach in cli.py).
        import asyncio as _aio
        import db as _db
        _stg = _aio.run(_db.get_settings(user_id)) or {}
        smtp_ok = bool(_stg.get("smtp_host") and _stg.get("smtp_user"))
    if not smtp_ok and not args.dry_run:
        print(_red("[huntova] SMTP not configured. Use --dry-run to preview."),
              file=sys.stderr)
        return 1
    res = asyncio.run(_run_once(user_id, dry_run=bool(args.dry_run),
                                max_send=int(args.max)))
    print()
    print(_bold("summary:"))
    print(f"  · {_green('sent')}   {res['sent']}")
    print(f"  · {_dim('skipped')} {res['skipped']}  (not due / no email / no opener)")
    print(f"  · {_yellow('paused')}  {res['paused']}  (replied / won / lost / manually paused)")
    if res['errored']:
        print(f"  · {_red('errored')} {res['errored']}")
    if res['dry_run']:
        print(f"  · {_dim('(dry-run mode — re-run without --dry-run to deliver)')}")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    from cli import _bootstrap_local_env
    user_id = _bootstrap_local_env()
    if user_id is None:
        return 1
    import db as _db
    leads = asyncio.run(_db.get_leads(user_id, limit=2000)) or []
    by_step = {0: 0, 1: 0, 2: 0, 3: 0}
    paused = 0
    for ld in leads:
        st = int(ld.get("_seq_step") or 0)
        by_step[st] = by_step.get(st, 0) + 1
        if ld.get("_seq_paused") or ld.get("email_status") in ("replied", "won", "meeting_booked"):
            paused += 1
    print(_bold("Sequence status\n"))
    print(f"  Step 0 (not enrolled): {by_step.get(0, 0)}")
    print(f"  Step 1 (opener sent):  {by_step.get(1, 0)}")
    print(f"  Step 2 (bump sent):    {by_step.get(2, 0)}")
    print(f"  Step 3 (final sent):   {by_step.get(3, 0)}")
    print(f"  {_yellow('paused')} (replied/won/manual): {paused}")
    return 0


def _cmd_pause(args: argparse.Namespace) -> int:
    from cli import _bootstrap_local_env
    user_id = _bootstrap_local_env()
    if user_id is None:
        return 1
    lid = (args.lead_id or "").strip()
    if not lid:
        print(_red("--lead-id required"), file=sys.stderr); return 1
    import db as _db
    def _mut(lead, _v=not args.resume):
        lead["_seq_paused"] = _v
        return lead
    res = asyncio.run(_db.merge_lead(user_id, lid, _mut))
    if res is None:
        print(_red(f"lead {lid} not found"), file=sys.stderr); return 1
    print(f"{_green('✓')} {lid} {'resumed' if args.resume else 'paused'}.")
    return 0


# ── argparse wiring ─────────────────────────────────────────────────

def register(subparsers) -> None:
    p = subparsers.add_parser(
        "sequence",
        help="3-step follow-up cadence (Day +4 bump, Day +9 final)",
        description=("Multi-step outreach. After `huntova outreach send` "
                     "fires the opener, this command sends the bump and "
                     "final messages on schedule. Auto-pauses the cadence "
                     "for any lead that replies (matched by `huntova "
                     "inbox watch`)."),
        epilog=("Examples:\n"
                "  huntova sequence run --dry-run\n"
                "  huntova sequence run --max 25\n"
                "  huntova sequence status\n"
                "  huntova sequence pause --lead-id L17\n"
                "  huntova sequence pause --lead-id L17 --resume\n\n"
                "Run via cron / launchd daily for hands-off cadence.\n"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="seq_cmd", required=True)

    p_run = sub.add_parser("run", help="send any due steps")
    p_run.add_argument("--dry-run", action="store_true",
                       help="preview without sending")
    p_run.add_argument("--max", default="50",
                       help="max emails to send this run [50]")
    p_run.set_defaults(func=_cmd_run)

    p_st = sub.add_parser("status", help="show how many leads are at each step")
    p_st.set_defaults(func=_cmd_status)

    p_pa = sub.add_parser("pause", help="pause / resume a single lead's cadence")
    p_pa.add_argument("--lead-id", required=True, help="lead id (e.g. L17)")
    p_pa.add_argument("--resume", action="store_true",
                      help="undo the pause")
    p_pa.set_defaults(func=_cmd_pause)
