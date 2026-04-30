"""
Huntova `teach` — guided good-fit / bad-fit flow that boosts the
smart-loop without dashboard clicks. Wired in cli.py via `register(sub)`.

Subcommands:
- `huntova teach`              5 random leads, arrow-key good/bad/skip,
                               one `lead_feedback` row per verdict +
                               DNA refinement at the end. Same code
                               path as `server.py /api/lead-feedback`.
- `huntova teach --import <csv>`  bulk import CSV (cols org_name,verdict).
                               Fuzzy-match by org_name; skip on no match.
- `huntova teach status`       counts + signals-until-next-DNA-refine.

Fresh huntova-only feature; not adapted from openclaw.
"""
from __future__ import annotations

import argparse
import csv
import json as _json
import random
import sys
from difflib import SequenceMatcher

# ── color helpers (mirror cli_approve.py / cli_memory.py shape) ──────
_TTY = sys.stdout.isatty()
_c = lambda code: ((lambda s: f"\033[{code}m{s}\033[0m") if _TTY else (lambda s: s))
_bold, _dim, _cyan, _green, _yellow, _red = (
    _c("1"), _c("2"), _c("36"), _c("32"), _c("33"), _c("31"))


def _safe_int(x) -> int:
    try: return int(x or 0)
    except Exception: return 0


def _fit_chip(score) -> str:
    n = _safe_int(score); label = f"[{n}/10]"
    if not _TTY: return label
    code = "1;32" if n >= 8 else "1;33" if n >= 6 else "2"
    return f"\033[{code}m{label}\033[0m"


# ── fuzzy match for --import ─────────────────────────────────────────
_LEGAL = (" inc", " inc.", " ltd", " ltd.", " llc", " gmbh", " sa",
          " s.a.", " srl", " s.r.l.", " bv", " ag", " co", " co.")


def _norm_org(s: str) -> str:
    """Lowercase + strip punctuation/legal suffixes for fuzzy match."""
    t = (s or "").lower().strip()
    for suf in _LEGAL:
        if t.endswith(suf): t = t[:-len(suf)].strip()
    return "".join(ch for ch in t if ch.isalnum() or ch == " ").strip()


def _fuzzy_find(needle: str, leads: list) -> dict | None:
    """Best-match by org_name. 0.78 threshold = 'Acme Corp' matches
    'Acme Corporation' but doesn't drift into noise."""
    target = _norm_org(needle)
    if not target: return None
    best = None; best_ratio = 0.0
    for lead in leads:
        cand = _norm_org(lead.get("org_name") or "")
        if not cand: continue
        if cand == target: return lead
        r = SequenceMatcher(None, target, cand).ratio()
        if r > best_ratio: best_ratio = r; best = lead
    return best if best_ratio >= 0.78 else None


# ── DNA refinement (mirrors server.py /api/lead-feedback) ────────────


def _maybe_refine_dna(user_id: int) -> dict | None:
    """If total feedback just hit a x10 mark, run DNA refinement.
    Returns the new DNA dict or None. Same code path as the dashboard's
    /api/lead-feedback POST handler in server.py."""
    import asyncio as _asyncio
    import db as _db

    async def _run() -> dict | None:
        counts = await _db.get_lead_feedback_count(user_id) or {}
        total = (counts.get("good") or 0) + (counts.get("bad") or 0)
        if total <= 0 or total % 10 != 0: return None
        try:
            settings = await _db.get_settings(user_id)
            wizard = (settings or {}).get("wizard", {}) or {}
            existing = await _db.get_agent_dna(user_id)
            good = await _db.get_lead_feedback_recent(user_id, "good", 10)
            bad = await _db.get_lead_feedback_recent(user_id, "bad", 10)
            from app import generate_agent_dna
            dna = await _asyncio.to_thread(
                generate_agent_dna, wizard, good, bad, existing)
            await _db.save_agent_dna(user_id, dna)
            return dna
        except Exception as e:
            print(f"[huntova] DNA refinement failed: {e}", file=sys.stderr)
            return None

    return _asyncio.run(_run())


# ── subcommands ──────────────────────────────────────────────────────


def _render_lead(lead: dict, idx: int, total: int) -> None:
    org = lead.get("org_name") or "(unknown)"
    why = (lead.get("why_fit") or "").strip()
    site = (lead.get("org_website") or "").strip()
    country = (lead.get("country") or "").strip()
    industry = (lead.get("industry") or lead.get("sector") or "").strip()
    print(f"\n  {_dim(f'{idx}/{total}')}  {_bold(org)}  "
          f"{_fit_chip(lead.get('fit_score'))}")
    if site:     print(f"    {_cyan('site:')}     {site}")
    if country:  print(f"    {_cyan('country:')}  {country}")
    if industry: print(f"    {_cyan('industry:')} {industry}")
    if why:      print(f"    {_cyan('why_fit:')}  {why[:200]}")


def _cmd_interactive(user_id: int, args: argparse.Namespace) -> int:
    import asyncio as _asyncio
    import db as _db
    leads = _asyncio.run(_db.get_leads(user_id))
    if not leads:
        print("[huntova] no leads to teach on yet — run `huntova hunt` first.")
        return 0
    n = max(1, min(_safe_int(args.count) or 5, 20))
    sample = random.sample(leads, min(n, len(leads)))
    try: from tui import intro, outro, select, SelectOption
    except Exception:
        print("[huntova] tui unavailable — install questionary or run a TTY.",
              file=sys.stderr)
        return 1
    intro(f"Teach the agent — {len(sample)} lead(s) to rate")
    print(f"  {_dim('Pick good fit / bad fit / skip for each. Boosts the smart-loop.')}")
    options = [
        SelectOption("good", _green("Good fit"), "use as a positive example"),
        SelectOption("bad",  _red("Bad fit"),    "use as a negative example"),
        SelectOption("skip", _yellow("Skip"),    "leave this one alone"),
        SelectOption("quit", _dim("Quit"),       "stop and save what you've rated"),
    ]
    good = bad = skipped = 0
    for i, lead in enumerate(sample, start=1):
        _render_lead(lead, i, len(sample))
        choice = select("Verdict?", options, default="skip")
        if choice in (None, "quit"): break
        if choice == "skip": skipped += 1; continue
        lid = lead.get("lead_id") or ""
        if not lid: skipped += 1; continue
        try:
            _asyncio.run(_db.save_lead_feedback(user_id, lid, choice, "cli-teach"))
            if choice == "good": good += 1
            else: bad += 1
        except Exception as e:  # pragma: no cover
            print(f"  {_red('!')} feedback save failed for {lid}: {e}", file=sys.stderr)
            skipped += 1
    outro(f"Recorded {_green(str(good))} good · {_red(str(bad))} bad · "
          f"{_dim(str(skipped) + ' skipped')}")
    if good + bad > 0:
        dna = _maybe_refine_dna(user_id)
        if dna:
            print(f"  {_green('●')} Agent DNA refined "
                  f"(v{dna.get('version', 1)}, "
                  f"{len(dna.get('search_queries', []))} queries).")
    return 0


def _cmd_import(user_id: int, args: argparse.Namespace) -> int:
    import asyncio as _asyncio
    import db as _db
    path = (args.import_path or "").strip()
    if not path:
        print("[huntova] usage: huntova teach --import <csv>", file=sys.stderr)
        return 1
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
    except OSError as e:
        print(f"[huntova] cannot open {path!r}: {e}", file=sys.stderr); return 1
    leads = _asyncio.run(_db.get_leads(user_id))
    if not leads:
        print("[huntova] no local leads to match against — run `huntova hunt` first.")
        return 0
    matched = skipped = invalid = 0
    samples: list[str] = []

    async def _run_bulk() -> None:
        nonlocal matched, skipped, invalid
        for row in rows:
            org = (row.get("org_name") or row.get("company") or "").strip()
            verdict = (row.get("verdict") or "").strip().lower()
            if verdict not in ("good", "bad") or not org:
                invalid += 1; continue
            hit = _fuzzy_find(org, leads)
            if not hit or not hit.get("lead_id"):
                skipped += 1
                if len(samples) < 5: samples.append(org)
                continue
            try:
                await _db.save_lead_feedback(
                    user_id, hit["lead_id"], verdict, "cli-teach-import")
                matched += 1
            except Exception as e:  # pragma: no cover
                invalid += 1
                print(f"  {_red('!')} save failed for {org!r}: {e}", file=sys.stderr)

    _asyncio.run(_run_bulk())
    print(f"\n  {_bold('Bulk teach import')}  {_dim(path)}")
    print(f"    matched   {_green(str(matched))}")
    print(f"    skipped   {_yellow(str(skipped))}  {_dim('(no fuzzy match)')}")
    print(f"    invalid   {_red(str(invalid))}  "
          f"{_dim('(missing org_name or bad verdict)')}")
    if samples:
        print(f"\n  {_dim('Unmatched samples:')}")
        for s in samples: print(f"    · {s[:60]}")
    if matched > 0:
        dna = _maybe_refine_dna(user_id)
        if dna:
            print(f"\n  {_green('●')} Agent DNA refined (v{dna.get('version', 1)}).")
    return 0


def _cmd_status(user_id: int, args: argparse.Namespace) -> int:
    import asyncio as _asyncio
    import db as _db
    counts = _asyncio.run(_db.get_lead_feedback_count(user_id)) or {}
    g = counts.get("good", 0) or 0; b = counts.get("bad", 0) or 0
    total = g + b
    until = (10 - (total % 10)) if total > 0 else 10
    if args.json:
        print(_json.dumps({"good": g, "bad": b, "total": total,
                           "until_next_dna_refinement": until}, indent=2))
        return 0
    print(f"\n  {_bold('Smart-loop feedback')}\n")
    print(f"    good       {_green(str(g))}")
    print(f"    bad        {_red(str(b))}")
    print(f"    total      {total}")
    if total == 0:
        print(f"\n  {_dim('Run `huntova teach` to start teaching the agent.')}")
    else:
        _msg = f"Next DNA refinement in {until} more signal(s) (every 10)."
        print(f"\n  {_dim(_msg)}")
    print("")
    return 0


# ── public dispatcher + argparse wiring ─────────────────────────────


def cmd_teach(args: argparse.Namespace) -> int:
    """Dispatcher: huntova teach {<interactive>|--import <csv>|status}."""
    from cli import _bootstrap_local_env
    user_id = _bootstrap_local_env()
    if user_id is None: return 1
    if (getattr(args, "teach_cmd", None) or "").strip() == "status":
        return _cmd_status(user_id, args)
    if getattr(args, "import_path", None):
        return _cmd_import(user_id, args)
    return _cmd_interactive(user_id, args)


def register(sub) -> None:
    """Attach the `teach` subparser. `status` is a real subparser;
    `--import` lives on the bare verb so `huntova teach --import x.csv`
    works without an extra positional."""
    t = sub.add_parser("teach",
        help="Teach the agent what good leads look like (smart-loop boost)",
        description="Guided good-fit / bad-fit flow that records "
                    "lead_feedback rows and triggers DNA refinement "
                    "every 10 signals — same path the dashboard buttons "
                    "use. Verbs: `<interactive>` / `--import <csv>` / `status`.")
    t.add_argument("--import", dest="import_path", default="", metavar="CSV",
                   help="Bulk import a CSV with columns org_name,verdict")
    t.add_argument("--count", type=int, default=5, metavar="N",
                   help="How many random leads to surface (default 5, max 20)")
    t.set_defaults(func=cmd_teach, teach_cmd="", json=False)
    t_sub = t.add_subparsers(dest="teach_cmd")
    s = t_sub.add_parser("status",
        help="Show current feedback counts + DNA refinement progress")
    s.add_argument("--json", action="store_true", help="Emit JSON")
    s.set_defaults(func=cmd_teach, teach_cmd="status", import_path="", count=0)
