"""
Huntova `memory` command — search / inspect / recent / stats.
Independent Python implementation specialised for Huntova's
lead + hunt + feedback tables. Wired in cli.py via `register(sub)`.
"""
from __future__ import annotations

import argparse
import json as _json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

# ── helpers ──────────────────────────────────────────────────────────

_TTY = sys.stdout.isatty()


def _c(code: str):
    return (lambda s: f"\033[{code}m{s}\033[0m") if _TTY else (lambda s: s)


_bold, _dim, _cyan, _green, _yellow, _red = (
    _c("1"), _c("2"), _c("36"), _c("32"), _c("33"), _c("31"))


def _safe_int(x) -> int:
    try: return int(x or 0)
    except Exception: return 0


def _fit_chip(score) -> str:
    n = _safe_int(score)
    label = f"[{n}/10]"
    if not _TTY: return label
    code = "1;32" if n >= 8 else "1;33" if n >= 6 else "2"
    return f"\033[{code}m{label}\033[0m"


def _score_bucket(score) -> str:
    n = _safe_int(score)
    if n >= 8: return "8-10 (strong)"
    if n >= 6: return "6-7 (qualified)"
    if n >= 4: return "4-5 (marginal)"
    return "0-3 (weak)"


_SEARCH_FIELDS = ("org_name", "why_fit", "production_gap",
                  "email_subject", "email_body")


def _score_match(lead: dict, terms: list[str]) -> int:
    """Sum of term-hits across searchable fields; org_name weighted 3x."""
    hay = {f: str(lead.get(f) or "").lower() for f in _SEARCH_FIELDS}
    score = 0
    for t in terms:
        for f, text in hay.items():
            if not text: continue
            hits = text.count(t)
            if hits: score += hits * (3 if f == "org_name" else 1)
    return score


# ── subcommands ──────────────────────────────────────────────────────


def _cmd_search(user_id: int, args: argparse.Namespace) -> int:
    import asyncio as _asyncio
    import db as _db
    query = (args.query or "").strip()
    if not query:
        print("[huntova] usage: huntova memory search <query>", file=sys.stderr)
        return 1
    terms = [t for t in query.lower().split() if t]
    leads = _asyncio.run(_db.get_leads(user_id))
    if not leads:
        print("[huntova] no leads in memory yet — run `huntova hunt` first.")
        return 0
    scored = [(l, s) for (l, s) in
              sorted(((l, _score_match(l, terms)) for l in leads),
                     key=lambda x: x[1], reverse=True) if s > 0][:20]
    if args.json:
        print(_json.dumps([{"lead_id": l.get("lead_id"), "org_name": l.get("org_name"),
                            "fit_score": l.get("fit_score"), "score": s,
                            "why_fit": l.get("why_fit")} for (l, s) in scored],
                          indent=2, default=str))
        return 0
    if not scored:
        print(f"[huntova] no leads in memory match {query!r}.")
        return 0
    print(f"\n{_bold(f'{len(scored)} match(es) for {query!r}:')}\n")
    for lead, s in scored:
        lid = (lead.get("lead_id") or "?")[:6].ljust(6)
        org = (lead.get("org_name") or "?")[:36]
        country = (lead.get("country") or "")[:12]
        why = (lead.get("why_fit") or "")[:80]
        print(f"  {_dim(lid)} {_fit_chip(lead.get('fit_score'))} "
              f"{_cyan(f'rank {s:>3}')}  {org:<36}  {_dim(country):<12}  {why}")
    print("")
    return 0


# ── inspect: colored YAML-like dump ─────────────────────────────────


def _yaml_scalar(v) -> str:
    """YAML-safe rendering. Multi-line uses block scalar; no yaml dep."""
    if v is None: return "~"
    if isinstance(v, bool): return "true" if v else "false"
    if isinstance(v, (int, float)): return str(v)
    s = str(v)
    if "\n" in s:
        return "|\n" + "\n".join(f"    {line}" for line in s.splitlines())
    if any(ch in s for ch in (":", "#", "&", "*", "!", "|", ">", "%", "@", "`")):
        return '"' + s.replace('"', '\\"') + '"'
    return s


_SCORE_KEYS = ("fit_score", "buyability_score", "timing_score")


def _emit_yaml(obj, indent: int = 0) -> None:
    """Stream colored YAML. Keys cyan; meta `_*` purple; scores green."""
    pad = "  " * indent
    if isinstance(obj, dict):
        for k, v in obj.items():
            ks = str(k)
            kr = (f"\033[35m{ks}\033[0m" if _TTY and ks.startswith("_")
                  else _cyan(ks))
            if isinstance(v, dict) and v:
                print(f"{pad}{kr}:"); _emit_yaml(v, indent + 1)
            elif isinstance(v, list) and v:
                print(f"{pad}{kr}:")
                for item in v:
                    if isinstance(item, (dict, list)):
                        print(f"{pad}  -"); _emit_yaml(item, indent + 2)
                    else:
                        print(f"{pad}  - {_yaml_scalar(item)}")
            else:
                r = _yaml_scalar(v)
                if "\n" not in r and ks in _SCORE_KEYS:
                    r = _green(r) if r != "~" else _dim(r)
                print(f"{pad}{kr}: {r}")
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, (dict, list)):
                print(f"{pad}-"); _emit_yaml(item, indent + 1)
            else:
                print(f"{pad}- {_yaml_scalar(item)}")


def _cmd_inspect(user_id: int, args: argparse.Namespace) -> int:
    import asyncio as _asyncio
    import db as _db
    target = (args.lead_id or "").strip()
    if not target:
        print("[huntova] usage: huntova memory inspect <lead_id>", file=sys.stderr)
        return 1
    lead = _asyncio.run(_db.get_lead(user_id, target))
    if not lead:
        print(f"[huntova] no lead with id {target!r}.", file=sys.stderr)
        return 1
    if args.json:
        print(_json.dumps(lead, indent=2, default=str))
        return 0
    org = lead.get("org_name") or "(unknown)"
    print(f"\n{_bold(org)}  {_dim(target)}\n")
    _emit_yaml(lead)
    print("")
    return 0


# ── recent: hunts + leads + feedback + emails in last N days ─────────


def _fetchall(drv, sql, params):
    conn = drv.get_conn()
    try:
        cur = conn.cursor()
        cur.execute(drv.translate_sql(sql), params)
        return [dict(r) for r in cur.fetchall()]
    finally: drv.put_conn(conn)


def _cmd_recent(user_id: int, args: argparse.Namespace) -> int:
    from db_driver import get_driver
    drv = get_driver()
    days = max(1, int(args.days or 7))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    hunts = _fetchall(drv,
        "SELECT id, status, leads_found, queries_done, queries_total, started_at "
        "FROM agent_runs WHERE user_id = %s AND started_at >= %s "
        "ORDER BY started_at DESC", (user_id, cutoff))
    leads = _fetchall(drv,
        "SELECT lead_id, org_name, fit_score, country, email_status, created_at "
        "FROM leads WHERE user_id = %s AND created_at >= %s "
        "ORDER BY created_at DESC LIMIT 200", (user_id, cutoff))
    feedback = _fetchall(drv,
        "SELECT lead_id, signal, reason, created_at FROM lead_feedback "
        "WHERE user_id = %s AND created_at >= %s "
        "ORDER BY created_at DESC LIMIT 200", (user_id, cutoff))
    emails = _fetchall(drv,
        "SELECT lead_id, action_type, created_at FROM lead_actions "
        "WHERE user_id = %s AND created_at >= %s "
        "AND action_type IN ('email_sent','email_followed_up','email_replied') "
        "ORDER BY created_at DESC LIMIT 200", (user_id, cutoff))
    if args.json:
        print(_json.dumps({"days": days, "hunts": hunts, "leads": leads,
                           "feedback": feedback, "emails": emails},
                          indent=2, default=str))
        return 0
    _ts = lambda r, k: (r.get(k) or "")[:19].replace("T", " ")
    print(f"\n{_bold(f'Last {days} day(s) of activity')}\n")
    print(f"{_bold('Hunts')} ({len(hunts)}):")
    if not hunts: print(f"  {_dim('— no runs')}")
    for h in hunts[:10]:
        print(f"  {_dim(_ts(h,'started_at'))}  run #{h.get('id','?')}  "
              f"status={h.get('status','?')}  leads={h.get('leads_found',0)}  "
              f"queries={h.get('queries_done',0)}/{h.get('queries_total',0)}")
    print(f"\n{_bold('Leads saved')} ({len(leads)}):")
    if not leads: print(f"  {_dim('— none')}")
    for l in leads[:15]:
        print(f"  {_dim(_ts(l,'created_at'))}  {_fit_chip(l.get('fit_score'))} "
              f"{(l.get('org_name') or '?')[:36]:<36}  "
              f"{(l.get('country') or '')[:12]:<12}  {l.get('email_status','')}")
    print(f"\n{_bold('Feedback signals')} ({len(feedback)}):")
    if not feedback: print(f"  {_dim('— none')}")
    for f in feedback[:15]:
        sig = f.get("signal") or "?"
        chip = _green(sig) if sig == "good" else _red(sig) if sig == "bad" else _dim(sig)
        print(f"  {_dim(_ts(f,'created_at'))}  {chip:<14} {f.get('lead_id','?'):<6}  "
              f"{(f.get('reason') or '')[:60]}")
    print(f"\n{_bold('Emails')} ({len(emails)}):")
    if not emails: print(f"  {_dim('— none')}")
    for e in emails[:15]:
        print(f"  {_dim(_ts(e,'created_at'))}  {e.get('action_type','?'):<20} "
              f"{e.get('lead_id','?')}")
    print("")
    return 0


# ── stats: aggregate counts ─────────────────────────────────────────


def _cmd_stats(user_id: int, args: argparse.Namespace) -> int:
    import asyncio as _asyncio
    import db as _db
    from db_driver import get_driver
    leads = _asyncio.run(_db.get_leads(user_id))
    total = len(leads)
    by_country = Counter((l.get("country") or "?").strip() or "?" for l in leads)
    by_industry = Counter((l.get("industry") or l.get("sector") or "?").strip() or "?"
                          for l in leads)
    by_bucket = Counter(_score_bucket(l.get("fit_score")) for l in leads)
    by_status = Counter((l.get("email_status") or "new").strip() or "new" for l in leads)
    sent_n = sum(by_status.get(s, 0) for s in
                 ("email_sent", "followed_up", "replied", "meeting_booked", "won"))
    replied_n = sum(by_status.get(s, 0) for s in ("replied", "meeting_booked", "won"))
    response_rate = round((replied_n / sent_n) * 100, 1) if sent_n else 0.0
    fb = _asyncio.run(_db.get_lead_feedback_count(user_id))
    runs_rows = _fetchall(get_driver(),
        "SELECT status, COUNT(*) as n FROM agent_runs WHERE user_id = %s GROUP BY status",
        (user_id,))
    runs_by_status = {(r.get("status") or "?"): int(r.get("n") or 0) for r in runs_rows}
    payload = {
        "total_leads": total,
        "by_country": dict(by_country.most_common(20)),
        "by_industry": dict(by_industry.most_common(20)),
        "by_fit_bucket": dict(by_bucket),
        "by_email_status": dict(by_status),
        "sent": sent_n, "replied": replied_n,
        "response_rate_pct": response_rate,
        "feedback": fb, "runs_by_status": runs_by_status,
    }
    if args.json:
        print(_json.dumps(payload, indent=2, default=str))
        return 0
    print(f"\n{_bold('Memory stats')}\n")
    print(f"  total leads        {_green(str(total))}")
    print(f"  emails sent        {sent_n}")
    print(f"  replies            {replied_n}")
    print(f"  response rate      {_yellow(f'{response_rate}%')}  {_dim('(replied/sent)')}")
    print(f"  good/bad feedback  {_green(str(fb.get('good', 0)))} / "
          f"{_red(str(fb.get('bad', 0)))}")

    def _table(title: str, c: Counter, top: int = 8):
        rows = c.most_common(top)
        if not rows: return
        print(f"\n  {_bold(title)}")
        for k, n in rows:
            print(f"    {(k or '?')[:30]:<30}  {n}")

    _table("By country", by_country)
    _table("By industry", by_industry)
    _table("By fit bucket", by_bucket)
    _table("By email status", by_status)
    if runs_by_status:
        print(f"\n  {_bold('Runs by status')}")
        for k, n in runs_by_status.items():
            print(f"    {k:<30}  {n}")
    print("")
    return 0


# ── public entrypoint, called from cli.py ────────────────────────────

_DISPATCH = {
    "search": _cmd_search, "inspect": _cmd_inspect,
    "recent": _cmd_recent, "stats": _cmd_stats,
}


def cmd_memory(args: argparse.Namespace) -> int:
    """Dispatcher: huntova memory {search|inspect|recent|stats}."""
    from cli import _bootstrap_local_env  # local import: defer DB init
    user_id = _bootstrap_local_env()
    if user_id is None:
        return 1
    fn = _DISPATCH.get((getattr(args, "memory_cmd", None) or "").strip())
    if fn: return fn(user_id, args)
    print("[huntova] usage: huntova memory {search <q>|inspect <id>|recent|stats}",
          file=sys.stderr)
    return 1


def register(sub) -> None:
    """Attach the `memory` subparser to cli.py's argparse tree."""
    m = sub.add_parser("memory",
        help="Search / inspect / summarise the local lead-gen corpus",
        description="Inspect leads, hunts, feedback, emails — full local-corpus search.")
    m_sub = m.add_subparsers(dest="memory_cmd")
    s = m_sub.add_parser("search", help="Fuzzy text search across saved leads")
    s.add_argument("query", help="Matches org_name/why_fit/production_gap/email_subject/email_body")
    s.add_argument("--json", action="store_true", help="Emit JSON instead of a table")
    s.set_defaults(func=cmd_memory, memory_cmd="search")
    i = m_sub.add_parser("inspect", help="Dump every field of one lead as colored YAML")
    i.add_argument("lead_id", help="The lead_id to inspect (e.g. L3)")
    i.add_argument("--json", action="store_true", help="Emit raw JSON instead of YAML")
    i.set_defaults(func=cmd_memory, memory_cmd="inspect")
    r = m_sub.add_parser("recent", help="Last N days of hunts, leads, feedback, emails")
    r.add_argument("--days", type=int, default=7, help="Look back N days (default 7)")
    r.add_argument("--json", action="store_true", help="Emit JSON instead of a report")
    r.set_defaults(func=cmd_memory, memory_cmd="recent")
    st = m_sub.add_parser("stats", help="Aggregate corpus stats")
    st.add_argument("--json", action="store_true", help="Emit JSON instead of a table")
    st.set_defaults(func=cmd_memory, memory_cmd="stats")
    # Bare `huntova memory` prints usage. cmd_memory needs these attrs.
    m.set_defaults(func=cmd_memory, memory_cmd="", json=False)
