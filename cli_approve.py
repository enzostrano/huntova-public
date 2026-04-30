"""
Huntova `approve` command — manual-approval queue for high-fit leads
before the agent fires their email. Safety feature for users who don't
want fully-autonomous outreach. Wired in cli.py via `register(sub)`.

Subcommands: `queue` (list pending), `<lead_id>` (approve one),
`--top N` (bulk-approve), `--reject <lead_id>`, `diff <lead_id>`.

The approve flow ONLY mutates `status`. Sending stays in
`huntova outreach send`; flipping status="approved" releases the email.
"""
from __future__ import annotations

import argparse
import json as _json
import sys

# ── color helpers (mirror cli_memory.py / cli_migrate.py shape) ──────

_TTY = sys.stdout.isatty()
_c = lambda code: ((lambda s: f"\033[{code}m{s}\033[0m") if _TTY else (lambda s: s))
_bold, _dim, _cyan, _green, _red = _c("1"), _c("2"), _c("36"), _c("32"), _c("31")


def _safe_int(x) -> int:
    try: return int(x or 0)
    except Exception: return 0


def _fit_chip(score) -> str:
    n = _safe_int(score); label = f"[{n}/10]"
    if not _TTY: return label
    code = "1;32" if n >= 8 else "1;33" if n >= 6 else "2"
    return f"\033[{code}m{label}\033[0m"


_SENT_STATUSES = {"email_sent", "followed_up", "replied", "meeting_booked", "won"}


def _is_pending(lead: dict) -> bool:
    """Pending iff never emailed AND (tagged awaiting_approval OR fit>=8)."""
    if (lead.get("email_status") or "new") in _SENT_STATUSES:
        return False
    status = (lead.get("status") or "").strip().lower()
    if status in ("approved", "rejected"):
        return False
    if status == "awaiting_approval":
        return True
    return _safe_int(lead.get("fit_score")) >= 8


def _set_status(user_id: int, lead_id: str, new_status: str) -> dict | None:
    """Atomic status mutation via db.merge_lead (row-locked RMW).
    Returns updated lead or None if not found."""
    import asyncio as _asyncio
    import db as _db

    def _mut(lead: dict) -> dict:
        lead["status"] = new_status
        return lead

    return _asyncio.run(_db.merge_lead(user_id, lead_id, _mut))


def _audit(user_id: int, lead_id: str, action_type: str,
           score_band: str = "", meta: dict | None = None) -> None:
    """Best-effort lead_actions row. Failure must never break approve."""
    import asyncio as _asyncio
    import db as _db
    try:
        _asyncio.run(_db.save_lead_action(
            user_id, lead_id, action_type,
            score_band=score_band, confidence_band="",
            meta=_json.dumps(meta or {}, default=str)))
    except Exception as e:  # pragma: no cover
        print(f"[huntova] audit log failed: {e}", file=sys.stderr)


def _score_band(score) -> str:
    n = _safe_int(score)
    if n >= 8: return "high"
    if n >= 6: return "medium"
    return "low"


# ── subcommands ──────────────────────────────────────────────────────


def _load_pending(user_id: int) -> list:
    import asyncio as _asyncio
    import db as _db
    leads = _asyncio.run(_db.get_leads(user_id))
    return sorted((l for l in leads if _is_pending(l)),
                  key=lambda l: _safe_int(l.get("fit_score")), reverse=True)


def _cmd_queue(user_id: int, args: argparse.Namespace) -> int:
    pending = _load_pending(user_id)
    if args.json:
        print(_json.dumps([{
            "lead_id": l.get("lead_id"), "org_name": l.get("org_name"),
            "fit_score": _safe_int(l.get("fit_score")),
            "contact_email": l.get("contact_email"),
            "why_fit": (l.get("why_fit") or ""),
            "status": l.get("status") or "",
        } for l in pending], indent=2, default=str))
        return 0
    if not pending:
        print("[huntova] approval queue is empty — no high-fit pending leads.")
        return 0
    print(f"\n{_bold(f'{len(pending)} lead(s) awaiting approval')}\n")
    print(f"  {_dim('id'):<8} {_dim('fit'):<10} {_dim('org_name'):<32} "
          f"{_dim('contact_email'):<34} {_dim('why_fit')}")
    for lead in pending:
        lid = (lead.get("lead_id") or "?")[:6].ljust(6)
        org = (lead.get("org_name") or "?")[:30].ljust(32)
        email = (lead.get("contact_email") or "")[:32].ljust(34)
        why = (lead.get("why_fit") or "")[:80]
        print(f"  {_dim(lid)}  {_fit_chip(lead.get('fit_score'))} "
              f"{org}  {email}  {why}")
    print("")
    return 0


def _approve_lead(user_id: int, lead_id: str, bulk: bool = False) -> dict | None:
    """Flip status + write audit row. Returns updated lead, None if not found."""
    updated = _set_status(user_id, lead_id, "approved")
    if not updated: return None
    _audit(user_id, lead_id, "approved",
           score_band=_score_band(updated.get("fit_score")),
           meta={"via": "cli_approve", "bulk": bulk} if bulk else {"via": "cli_approve"})
    return updated


def _cmd_approve_one(user_id: int, args: argparse.Namespace) -> int:
    target = (args.lead_id or "").strip()
    if not target:
        print("[huntova] usage: huntova approve <lead_id>", file=sys.stderr); return 1
    lead = _approve_lead(user_id, target)
    if not lead:
        print(f"[huntova] no lead with id {target!r}.", file=sys.stderr); return 1
    if args.json:
        print(_json.dumps({"lead_id": target, "status": "approved",
                           "org_name": lead.get("org_name"),
                           "fit_score": _safe_int(lead.get("fit_score"))},
                          indent=2, default=str))
        return 0
    print(f"{_green('approved')}  {target}  {_fit_chip(lead.get('fit_score'))}  "
          f"{(lead.get('org_name') or '?')[:50]}")
    print(_dim("  → next `huntova outreach send` will release this email"))
    return 0


def _cmd_top(user_id: int, args: argparse.Namespace) -> int:
    pending = _load_pending(user_id)[:max(1, _safe_int(args.top))]
    if not pending:
        print("[huntova] approval queue is empty — nothing to bulk-approve."); return 0
    # Single asyncio loop wraps the whole bulk-approve. Previous version
    # called _approve_lead per-lead, each spawning two _asyncio.run() calls
    # (status mutation + audit row). For --top 100 that's 200 loop creations
    # + 200 pool checkouts, starving any concurrent agent thread on
    # PostgreSQL maxconn=10. Round-6 audit finding #2 — same anti-pattern
    # cli_migrate fixed in v0.1.0a6.
    import asyncio as _asyncio
    import db as _db

    async def _run_bulk() -> list:
        out: list = []
        for l in pending:
            lid = l.get("lead_id")
            if not lid: continue
            updated = await _db.merge_lead(user_id, lid, lambda d: dict(d, status="approved"))
            if not updated: continue
            try:
                await _db.save_lead_action(
                    user_id, lid, "approved",
                    score_band=_score_band(updated.get("fit_score")),
                    confidence_band="",
                    meta=_json.dumps({"via": "cli_approve", "bulk": True}, default=str))
            except Exception as e:  # pragma: no cover
                print(f"[huntova] audit log failed for {lid}: {e}", file=sys.stderr)
            out.append(updated)
        return out

    approved = _asyncio.run(_run_bulk())
    if args.json:
        print(_json.dumps([{
            "lead_id": l.get("lead_id"), "org_name": l.get("org_name"),
            "fit_score": _safe_int(l.get("fit_score")),
        } for l in approved], indent=2, default=str))
        return 0
    print(f"\n{_bold(f'Bulk-approved {len(approved)} lead(s)')}\n")
    for lead in approved:
        lid = (lead.get("lead_id") or "?")[:6].ljust(6)
        print(f"  {_green('+')} {_dim(lid)}  {_fit_chip(lead.get('fit_score'))}  "
              f"{(lead.get('org_name') or '?')[:50]}")
    print("")
    return 0


def _cmd_reject(user_id: int, args: argparse.Namespace) -> int:
    target = (args.reject or "").strip()
    if not target:
        print("[huntova] usage: huntova approve --reject <lead_id>", file=sys.stderr); return 1
    lead = _set_status(user_id, target, "rejected")
    if not lead:
        print(f"[huntova] no lead with id {target!r}.", file=sys.stderr); return 1
    _audit(user_id, target, "rejected",
           score_band=_score_band(lead.get("fit_score")),
           meta={"via": "cli_approve"})
    if args.json:
        print(_json.dumps({"lead_id": target, "status": "rejected",
                           "org_name": lead.get("org_name")}, indent=2, default=str))
        return 0
    print(f"{_red('rejected')}  {target}  {(lead.get('org_name') or '?')[:50]}")
    print(_dim("  → counts as feedback for the smart-loop"))
    return 0


def _cmd_diff(user_id: int, args: argparse.Namespace) -> int:
    """Side-by-side: AI draft email vs source-page evidence quote."""
    import asyncio as _asyncio
    import db as _db
    target = (args.lead_id or "").strip()
    if not target:
        print("[huntova] usage: huntova approve diff <lead_id>", file=sys.stderr)
        return 1
    lead = _asyncio.run(_db.get_lead(user_id, target))
    if not lead:
        print(f"[huntova] no lead with id {target!r}.", file=sys.stderr)
        return 1
    subject = (lead.get("email_subject") or "").strip()
    body = (lead.get("email_body") or "").strip()
    evidence = (lead.get("evidence_quote") or "").strip()
    why_fit = (lead.get("why_fit") or "").strip()
    source = (lead.get("contact_page_url") or lead.get("org_website") or "").strip()
    if args.json:
        print(_json.dumps({
            "lead_id": target, "org_name": lead.get("org_name"),
            "fit_score": _safe_int(lead.get("fit_score")),
            "contact_email": lead.get("contact_email"),
            "draft": {"subject": subject, "body": body},
            "evidence": {"quote": evidence, "why_fit": why_fit, "source": source},
        }, indent=2, default=str))
        return 0
    org = lead.get("org_name") or "(unknown)"
    print(f"\n{_bold(org)}  {_dim(target)}  {_fit_chip(lead.get('fit_score'))}\n")
    print(f"{_bold('AI draft email')}")
    print(f"  {_cyan('to:')}      {lead.get('contact_email') or _dim('(none)')}")
    print(f"  {_cyan('subject:')} {subject or _dim('(none)')}")
    print(f"  {_cyan('body:')}")
    for ln in (body.splitlines() if body else [_dim('(no body drafted)')]):
        print(f"    {ln}")
    print(f"\n{_bold('Source evidence')}")
    print(f"  {_cyan('source:')}    {source or _dim('(no url on file)')}")
    print(f"  {_cyan('why_fit:')}   {why_fit or _dim('(none)')}")
    print(f"  {_cyan('evidence:')}  {evidence or _dim('(none)')}\n")
    return 0


# ── public dispatcher + argparse wiring ──────────────────────────────


def cmd_approve(args: argparse.Namespace) -> int:
    """Dispatcher: huntova approve {queue|diff|<lead_id>|--top|--reject}."""
    from cli import _bootstrap_local_env
    user_id = _bootstrap_local_env()
    if user_id is None: return 1
    # First positional doubles as verb (queue/diff) OR a lead_id.
    target = (getattr(args, "lead_id", None) or "").strip()
    second = (getattr(args, "lead_id2", None) or "").strip()
    if target == "queue": return _cmd_queue(user_id, args)
    if target == "diff":
        if not second:
            print("[huntova] usage: huntova approve diff <lead_id>", file=sys.stderr)
            return 1
        args.lead_id = second
        return _cmd_diff(user_id, args)
    if getattr(args, "top", None):     return _cmd_top(user_id, args)
    if getattr(args, "reject", None):  return _cmd_reject(user_id, args)
    if target:                         return _cmd_approve_one(user_id, args)
    print("[huntova] usage: huntova approve {queue|diff <id>|<id>|"
          "--top N|--reject <id>}", file=sys.stderr)
    return 1


def register(sub) -> None:
    """Attach the `approve` subparser to cli.py's argparse tree.
    Avoids `add_subparsers` because verbs (queue/diff) collide with
    the positional <lead_id> slot — we route through cmd_approve()."""
    a = sub.add_parser("approve",
        help="Manual-approval queue for high-fit leads before outreach send",
        description="Review high-fit leads, approve or reject before the "
                    "agent emails them. Verbs: `queue` / `diff <id>` / "
                    "`<id>` / `--top N` / `--reject <id>`. Side effect of "
                    "approve: next `huntova outreach send` releases it.")
    a.add_argument("lead_id", nargs="?", default="",
                   help="Verb (queue|diff) OR a lead_id to approve")
    a.add_argument("lead_id2", nargs="?", default="",
                   help="When verb is `diff`, the lead_id to inspect")
    a.add_argument("--top", type=int, default=0, metavar="N",
                   help="Bulk-approve top-N highest-fit pending leads")
    a.add_argument("--reject", default="", metavar="LEAD_ID",
                   help="Reject a lead (status=rejected, smart-loop feedback)")
    a.add_argument("--json", action="store_true", help="Emit JSON")
    a.set_defaults(func=cmd_approve)
