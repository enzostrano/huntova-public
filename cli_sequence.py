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


# ── cadence config (built-in, override via Settings → Sequences) ──

# (step_number, delay_days_after_previous, template_id)
_CADENCE = [
    (2, 4, "bump"),
    (3, 5, "final"),  # 9 days from start = 4 + 5 from step 2
]


def _user_cadence(settings: dict | None) -> list[tuple[int, int, str]]:
    """a248: build the cadence list from user Settings → Sequences fields.
    Falls back to the built-in `_CADENCE` when the user hasn't set days,
    so existing installs keep their current rhythm. Returns empty list
    when sequence_enabled == "off"."""
    if not settings:
        return list(_CADENCE)
    if str(settings.get("sequence_enabled", "")).strip().lower() == "off":
        return []
    out = []
    try:
        d1 = int(settings.get("follow_up_1_days") or 4)
        out.append((2, max(0, d1), "bump"))
    except Exception:
        out.append(_CADENCE[0])
    try:
        d2 = int(settings.get("follow_up_2_days") or 9)
        out.append((3, max(0, d2 - out[0][1]), "final"))
    except Exception:
        out.append(_CADENCE[1])
    try:
        d3 = settings.get("follow_up_3_days")
        if d3:
            d3i = int(d3)
            out.append((4, max(0, d3i - (out[0][1] + out[1][1])), "final2"))
    except Exception:
        pass
    return out

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


def _due(_seq_step: int, _seq_last_at: str | None, _cadence: list | None = None) -> bool:
    """Is the next step due *now*?

    a248: accepts optional `_cadence` so caller can pass a user-tuned
    cadence built from Settings → Sequences. Falls back to the built-in
    `_CADENCE` when not supplied.
    """
    if not _seq_last_at:
        return False
    try:
        last = datetime.fromisoformat(_seq_last_at.replace("Z", "+00:00"))
    except Exception:
        return False
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    cad = _cadence if _cadence is not None else _CADENCE
    delta_days = next((d for s, d, _ in cad if s == _seq_step + 1), None)
    if delta_days is None:
        return False
    return (datetime.now(timezone.utc) - last) >= timedelta(days=delta_days)


def _template_for(next_step: int, settings: dict | None = None) -> tuple[str, str]:
    """Return (template_id, template_text) for the given step.

    a248: when the user has saved a per-step template under Settings →
    Sequences (`follow_up_1_template` / `_2_template` / `_3_template`),
    use it instead of the bundled defaults.
    """
    cad = _user_cadence(settings) if settings is not None else _CADENCE
    for step, _, tid in cad:
        if step == next_step:
            if settings:
                _key = {"bump": "follow_up_1_template",
                        "final": "follow_up_2_template",
                        "final2": "follow_up_3_template"}.get(tid)
                if _key:
                    _override = (settings.get(_key) or "").strip()
                    if _override:
                        return tid, _override
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
    # a248: send-window gate — refuse to send outside the user's
    # configured send hours so a cron firing at 03:00 UTC doesn't blast
    # prospects in their dinner hour. Times are in `send_timezone` (or
    # UTC if missing). When unset, no gate applied — full backwards
    # compat with installs that didn't set Settings → Email yet.
    try:
        _start = s.get("send_window_start")
        _end = s.get("send_window_end")
        if _start is not None and _end is not None:
            # a266: fall back to Profile → Timezone if send_timezone unset.
            # The Settings UI consolidated to a single timezone field
            # under Profile (was confusingly duplicated as send_timezone).
            _tz_name = (s.get("send_timezone") or s.get("timezone") or "UTC").strip() or "UTC"
            try:
                from zoneinfo import ZoneInfo
                _tz = ZoneInfo(_tz_name)
            except Exception:
                _tz = timezone.utc
            now_local = datetime.now(_tz).hour
            _s_h = int(_start); _e_h = int(_end)
            # Allow 8-17 (normal) or 22-6 (overnight wrap)
            in_window = (_s_h <= now_local < _e_h) if _s_h <= _e_h else (now_local >= _s_h or now_local < _e_h)
            if not in_window:
                return {"sent": 0, "skipped": 0, "paused": 0, "errors": 0,
                        "deferred_reason": f"outside send window {_s_h}-{_e_h} {_tz_name}"}
    except Exception:
        pass
    # a248: daily send cap — respect user's daily ceiling. Use the
    # smaller of (max_send arg, daily_send_cap setting) so the CLI
    # `--max` flag can still cap below the configured daily limit.
    try:
        _cap = int(s.get("daily_send_cap") or 0)
        if _cap and _cap < max_send:
            max_send = _cap
    except Exception:
        pass

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

    # a262: derive the cadence ceiling from Settings → Sequences instead
    # of hardcoding step >= 3. Previously a user who set follow_up_3_days
    # + follow_up_3_template would still see the step-4 send never fire
    # because the cap was a literal 3. Now the ceiling tracks whatever
    # _user_cadence emits (built-in 2 steps OR up to 3 when follow_up_3
    # is configured).
    _cad = _user_cadence(s)
    _max_step = max((sn for sn, _, _ in _cad), default=0) if _cad else 0
    # a262: respect Settings → Sequences gates. stop_on_reply and
    # stop_on_unsubscribe were saved correctly but never read — the
    # cadence loop hardcoded the skip on every terminal status. Now
    # the user can opt out of either gate by setting it to "off".
    _stop_reply = (s.get("stop_on_reply") or "").strip().lower() != "off"
    _stop_unsub = (s.get("stop_on_unsubscribe") or "").strip().lower() != "off"
    _terminal = {"won", "meeting_booked", "lost", "ignored"}
    sent, skipped, paused, errored, persist_failed = 0, 0, 0, 0, 0
    for ld in leads or []:
        if sent >= max_send:
            break
        step = int(ld.get("_seq_step") or 0)
        if step <= 0 or step >= _max_step:
            skipped += 1
            continue
        if ld.get("_seq_paused"):
            paused += 1
            continue
        _status = ld.get("email_status")
        if _status in _terminal:
            paused += 1
            continue
        if _status == "replied" and _stop_reply:
            paused += 1
            continue
        if _status == "unsubscribed" and _stop_unsub:
            paused += 1
            continue
        if not _cad:
            paused += 1
            continue
        if not _due(step, ld.get("_seq_last_at") or ld.get("_sent_at"), _cad):
            skipped += 1
            continue

        next_step = step + 1
        tid, tmpl = _template_for(next_step, s)
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
        # a262: append signature / compliance footer / opt-out phrase on
        # follow-ups too. a250+a261 fixed this on the initial cold email
        # paths (generate_tone_email AI-success + template fallback) but
        # cli_sequence had the same gap — every follow-up shipped without
        # the user's configured Settings → Email block, defeating the
        # whole point of having one.
        _sig = (s.get("email_signature") or "").strip()
        _ftr = (s.get("email_footer") or "").strip()
        _oo  = (s.get("opt_out_text") or "").strip()
        if _sig: body = body.rstrip() + "\n\n" + _sig
        if _ftr: body = body.rstrip() + "\n\n" + _ftr
        if _oo:  body = body.rstrip() + "\n\n" + _oo
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
                lead["_message_id"] = (_mid or "").strip("<>")
            lead["_sent_at"] = _ts
            lead["_sent_to"] = _to
            return lead

        # a274: track post-send persistence outcome separately. Pre-a274
        # this block printed-and-swallowed any merge_lead / save_lead_action
        # failure, then unconditionally `sent += 1`. Net effect: the email
        # left the SMTP server but `_seq_step` / `_message_id` weren't
        # stamped — next cron tick re-sent the same follow-up to the same
        # prospect. Caller still saw `{"ok": True, "sent": N}` with no
        # signal that a duplicate-send loop was brewing. Now we count
        # persist failures separately and surface them in the result.
        _persist_ok = True
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
            print(f"    {_yellow('!')}  email LEFT the SMTP server but _seq_step / _message_id weren't stamped.")
            print(f"    {_yellow('!')}  Next sequence run could re-send. Investigate before re-running.")
            _persist_ok = False
            persist_failed += 1

        # Only count toward `sent` when persistence succeeded — otherwise
        # the caller's "sent N" report is honest about what was durably
        # advanced. The email going out is recorded under `persist_failed`.
        if _persist_ok:
            sent += 1

    return {"ok": True, "sent": sent, "skipped": skipped,
            "paused": paused, "errored": errored,
            "persist_failed": persist_failed, "dry_run": dry_run}


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
                                max_send=int(args.max or 50)))
    print()
    print(_bold("summary:"))
    print(f"  · {_green('sent')}   {res['sent']}")
    print(f"  · {_dim('skipped')} {res['skipped']}  (not due / no email / no opener)")
    print(f"  · {_yellow('paused')}  {res['paused']}  (replied / won / lost / manually paused)")
    if res['errored']:
        print(f"  · {_red('errored')} {res['errored']}")
    # a289 fix: surface persist_failed in the human summary. Was
    # silently dropped — caller had no way to know an email left SMTP
    # but the lead's `_seq_step` / `_message_id` weren't stamped, which
    # means the next cron tick will re-send the same email.
    if res.get('persist_failed'):
        print(f"  · {_red('persist_failed')} {res['persist_failed']}  "
              f"(email sent but state NOT stamped — risk of duplicate next run; "
              f"check the warnings above to identify affected leads)")
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
    # a291 fix: type=int — was string, int() inside the handler raised
    # ValueError on non-numeric input.
    p_run.add_argument("--max", type=int, default=50,
                       help="max emails to send this run [50]")
    p_run.set_defaults(func=_cmd_run)

    p_st = sub.add_parser("status", help="show how many leads are at each step")
    p_st.set_defaults(func=_cmd_status)

    p_pa = sub.add_parser("pause", help="pause / resume a single lead's cadence")
    p_pa.add_argument("--lead-id", required=True, help="lead id (e.g. L17)")
    p_pa.add_argument("--resume", action="store_true",
                      help="undo the pause")
    p_pa.set_defaults(func=_cmd_pause)
