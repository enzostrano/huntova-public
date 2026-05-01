"""huntova pulse — weekly self-coaching summary.

Reads from the local SQLite DB (no schema changes, no extra deps)
and prints a one-screen breakdown of the user's outreach pulse:

  * hunts run + leads found
  * average fit_score / top fit_score
  * emails sent (Step 1 / 2 / 3 by sequence step)
  * replies by class (interested / not_now / not_interested / etc.)
  * conversion-rate proxy (replies / emails sent)
  * suggested next action

The summary defaults to the last 7 days. `--since 30d` widens the
window. `--json` emits machine-readable output for scripting.

Adapted from the OpenClaw `recent` pattern but specialised for the
lead-gen pipeline shape.
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
def _cyan(s: str) -> str: return f"\033[36m{s}\033[0m"


def _parse_since(s: str) -> timedelta:
    """Accept '7d', '30d', '24h', '2w'. Fallback to 7 days."""
    s = (s or "").strip().lower()
    if not s:
        return timedelta(days=7)
    try:
        if s.endswith("h"):
            return timedelta(hours=int(s[:-1]))
        if s.endswith("d"):
            return timedelta(days=int(s[:-1]))
        if s.endswith("w"):
            return timedelta(weeks=int(s[:-1]))
        return timedelta(days=int(s))
    except Exception:
        return timedelta(days=7)


def _parse_iso(value) -> datetime | None:
    if not value:
        return None
    try:
        s = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


# ── core compute ───────────────────────────────────────────────────

async def _compute(user_id: int, since: timedelta) -> dict:
    import db as _db
    cutoff = datetime.now(timezone.utc) - since

    leads = await _db.get_leads(user_id, limit=2000) or []
    new_leads = [
        l for l in leads
        if (_parse_iso(l.get("found_date") or l.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc)) >= cutoff
    ]
    fit_scores = [int(l.get("fit_score") or 0) for l in new_leads if l.get("fit_score")]

    # Replies by class — read straight off lead row (set by inbox watch).
    by_class: dict[str, int] = {}
    for l in leads:
        d = _parse_iso(l.get("_reply_date"))
        if not d or d < cutoff:
            continue
        klass = (l.get("_reply_class") or "interested").lower()
        by_class[klass] = by_class.get(klass, 0) + 1

    # Emails sent by sequence step.
    sent_by_step = {1: 0, 2: 0, 3: 0}
    for l in leads:
        st = int(l.get("_seq_step") or 0)
        d = _parse_iso(l.get("_seq_last_at") or l.get("_sent_at"))
        if st > 0 and d and d >= cutoff:
            sent_by_step[st] = sent_by_step.get(st, 0) + 1
    total_sent = sum(sent_by_step.values())
    total_replies = sum(by_class.values())

    # Status breakdown right now (snapshot, not windowed).
    status_now: dict[str, int] = {}
    for l in leads:
        s = (l.get("email_status") or "new").lower()
        status_now[s] = status_now.get(s, 0) + 1

    # High-fit but unsent — the actionable backlog.
    untouched_top = [
        l for l in leads
        if int(l.get("fit_score") or 0) >= 8
        and not int(l.get("_seq_step") or 0)
        and (l.get("contact_email") or "").strip()
    ][:5]

    return {
        "since_days": int(since.total_seconds() / 86400),
        "leads_found": len(new_leads),
        "fit_avg": (sum(fit_scores) / len(fit_scores)) if fit_scores else 0,
        "fit_max": max(fit_scores) if fit_scores else 0,
        "sent_by_step": sent_by_step,
        "total_sent": total_sent,
        "by_class": by_class,
        "total_replies": total_replies,
        "reply_rate": (total_replies / total_sent) if total_sent else 0,
        "status_now": status_now,
        "untouched_top": [
            {"lead_id": l.get("lead_id"), "org_name": l.get("org_name"),
             "fit_score": l.get("fit_score")}
            for l in untouched_top
        ],
    }


# ── pretty-print ───────────────────────────────────────────────────

def _format_human(p: dict) -> str:
    lines: list[str] = []
    win = p["since_days"]
    lines.append("")
    lines.append(_bold(f"Huntova pulse — last {win} day{'s' if win != 1 else ''}"))
    lines.append("")
    lines.append(f"  {_cyan('discovery')}")
    lines.append(f"    leads found     {_green(str(p['leads_found']))}")
    if p["fit_avg"]:
        lines.append(f"    avg fit_score   {p['fit_avg']:.1f}")
        lines.append(f"    top fit_score   {p['fit_max']}")
    lines.append("")

    sent = p["sent_by_step"]
    if any(sent.values()):
        lines.append(f"  {_cyan('outreach')}")
        lines.append(f"    Step 1 (opener) {sent.get(1, 0)}")
        lines.append(f"    Step 2 (bump)   {sent.get(2, 0)}")
        lines.append(f"    Step 3 (final)  {sent.get(3, 0)}")
        lines.append(f"    total           {_green(str(p['total_sent']))}")
        lines.append("")

    bc = p["by_class"]
    if bc:
        lines.append(f"  {_cyan('replies')}")
        for klass in ("interested", "not_now", "not_interested",
                       "wrong_person", "unsubscribe", "out_of_office"):
            n = bc.get(klass, 0)
            if not n:
                continue
            color = (_green if klass in ("interested", "not_now")
                     else _yellow if klass in ("not_interested", "wrong_person", "unsubscribe")
                     else _dim)
            lines.append(f"    {color(klass):28s} {n}")
        if p["total_sent"]:
            lines.append(f"    {_dim('reply rate')}      "
                         f"{p['reply_rate'] * 100:.1f}% ({p['total_replies']}/{p['total_sent']})")
        lines.append("")

    sn = p["status_now"]
    if sn:
        lines.append(f"  {_cyan('CRM snapshot')}")
        for s in ("new", "email_sent", "followed_up", "replied",
                   "meeting_booked", "won", "lost", "ignored"):
            n = sn.get(s, 0)
            if not n:
                continue
            lines.append(f"    {s:20s} {n}")
        lines.append("")

    # ── next-action recommendation ────────────────────────────────
    lines.append(f"  {_cyan('next action')}")
    suggested = False
    if p["leads_found"] == 0:
        lines.append(f"    {_yellow('!')} no new leads in this window — "
                     f"try {_bold('huntova hunt --max-leads 10')}")
        suggested = True
    elif p["untouched_top"]:
        n = len(p["untouched_top"])
        ids = ", ".join(l["lead_id"] for l in p["untouched_top"] if l.get("lead_id"))
        lines.append(f"    {_green('▸')} {n} top-tier lead{'s' if n != 1 else ''} "
                     f"(fit ≥ 8) ready for deep-research outreach: {_dim(ids)}")
        lines.append(f"      run {_bold('huntova outreach send --top ' + str(n) + ' --research-above 8')}")
        suggested = True
    if p["total_sent"] and p["total_replies"]:
        lines.append(f"    {_green('▸')} {p['total_replies']} repl{'y' if p['total_replies'] == 1 else 'ies'} "
                     f"to triage — {_bold('huntova ls --filter status:replied')}")
        suggested = True
    if p["total_sent"] >= 5 and p["total_replies"] == 0 and p["since_days"] >= 7:
        lines.append(f"    {_yellow('!')} {p['total_sent']} sent, no replies yet — "
                     f"check deliverability with {_bold('huntova doctor --email')}")
        suggested = True
    if not suggested:
        lines.append(f"    {_dim('all clear — keep hunting.')}")
    lines.append("")
    return "\n".join(lines)


def _cmd_pulse(args: argparse.Namespace) -> int:
    from cli import _bootstrap_local_env
    user_id = _bootstrap_local_env()
    if user_id is None:
        return 1
    since = _parse_since(getattr(args, "since", "7d"))
    p = asyncio.run(_compute(user_id, since))
    if getattr(args, "json", False):
        import json as _json
        print(_json.dumps(p, indent=2, default=str))
        return 0
    print(_format_human(p))
    return 0


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "pulse",
        help="weekly self-coaching summary (leads, replies, conversion, next-action)",
        description=("One-screen overview of your outreach pulse. "
                     "Pulls from the local DB; no extra deps. Recommends "
                     "the next concrete command to run."),
        epilog=("Examples:\n"
                "  huntova pulse                  # last 7 days\n"
                "  huntova pulse --since 30d\n"
                "  huntova pulse --since 24h --json | jq\n\n"
                "Pair with `huntova daemon` to wake daily and run pulse "
                "as a cron-style nudge.\n"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--since", default="7d",
                   help="window (e.g. 24h, 7d, 30d, 2w) [7d]")
    p.add_argument("--json", action="store_true",
                   help="machine-readable output")
    p.set_defaults(func=_cmd_pulse)
