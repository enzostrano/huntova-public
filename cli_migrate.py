"""
Huntova `migrate` command — import leads from another lead-gen system.
Pattern adapted from OpenClaw's `openclaw migrate`; fresh Python
implementation specialised for Huntova's lead schema. Wired in cli.py
via `register(sub)`. Uses stdlib `csv` only — no new deps.
Subcommands: from-csv | from-apollo | from-clay | from-hunter | stats.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import sys
from datetime import datetime, timezone

# ── color helpers (mirror cli_memory.py shape) ────────────────────────

_TTY = sys.stdout.isatty()


def _c(code: str):
    return (lambda s: f"\033[{code}m{s}\033[0m") if _TTY else (lambda s: s)


_bold, _dim, _cyan, _green, _yellow, _red = (
    _c("1"), _c("2"), _c("36"), _c("32"), _c("33"), _c("31"))


# ── canonical Huntova lead fields the importer can populate ──────────

_LEAD_FIELDS = {
    "org_name", "org_website", "country", "industry", "sector",
    "contact_name", "contact_email", "contact_role", "contact_linkedin",
    "fit_score", "why_fit", "production_gap", "event_name",
    "company_size", "linkedin_url", "phone", "city", "region",
    "first_name", "last_name", "title", "description",
}

# ── predefined column maps for known sources ─────────────────────────

# Apollo CSV export — column names from app.apollo.io exporter.
APOLLO_MAP = {
    "Company": "org_name", "Website": "org_website",
    "Industry": "industry", "# Employees": "company_size",
    "Country": "country", "City": "city", "State": "region",
    "First Name": "first_name", "Last Name": "last_name",
    "Title": "contact_role", "Email": "contact_email",
    "Person Linkedin Url": "contact_linkedin",
    "Person LinkedIn Url": "contact_linkedin",
    "Person LinkedIn URL": "contact_linkedin",
    "Company Linkedin Url": "linkedin_url",
    "Corporate Phone": "phone",
}

# Clay export — wider schema, varies by user template; common columns.
CLAY_MAP = {
    "Company Name": "org_name", "Company": "org_name",
    "Domain": "org_website", "Website": "org_website",
    "Company Website": "org_website", "Industry": "industry",
    "Employees": "company_size", "Headcount": "company_size",
    "Country": "country", "City": "city", "Region": "region",
    "Full Name": "contact_name", "Name": "contact_name",
    "First Name": "first_name", "Last Name": "last_name",
    "Title": "contact_role", "Job Title": "contact_role",
    "Email": "contact_email", "Work Email": "contact_email",
    "LinkedIn URL": "contact_linkedin",
    "Person LinkedIn": "contact_linkedin",
    "Company LinkedIn": "linkedin_url", "Phone": "phone",
    "Description": "description", "Company Description": "description",
}

# Hunter.io email-finder export — narrow, mostly email-focused.
HUNTER_MAP = {
    "first_name": "first_name", "last_name": "last_name",
    "email": "contact_email", "position": "contact_role",
    "company": "org_name", "domain": "org_website",
    "linkedin": "contact_linkedin", "linkedin_url": "contact_linkedin",
    "phone_number": "phone",
}

# ── auto-detect heuristic for generic CSV ────────────────────────────

# Match each canonical field against header substrings (lowercased).
_HEURISTIC = [
    ("org_website",   ("website", "domain", "url", "site")),
    ("contact_email", ("email", "e-mail", "mail")),
    ("contact_linkedin", ("linkedin",)),
    ("contact_role",  ("title", "role", "position", "job")),
    ("contact_name",  ("contact name", "full name", "name")),
    ("first_name",    ("first name", "firstname", "first_name")),
    ("last_name",     ("last name", "lastname", "last_name", "surname")),
    ("org_name",      ("company", "organization", "organisation", "org",
                       "account", "employer")),
    ("country",       ("country",)),
    ("city",          ("city", "town")),
    ("region",        ("region", "state", "province")),
    ("industry",      ("industry", "sector", "vertical")),
    ("company_size",  ("size", "employees", "headcount")),
    ("phone",         ("phone", "tel", "mobile")),
    ("fit_score",     ("score", "fit",)),
    ("why_fit",       ("why", "reason", "rationale")),
    ("description",   ("description", "summary", "about")),
]


def _autodetect(headers) -> dict:
    out, used = {}, set()
    for canon, needles in _HEURISTIC:
        if canon in out.values():
            continue
        for h in headers:
            if h in used: continue
            low = (h or "").strip().lower()
            if not low: continue
            if any(n in low for n in needles):
                out[h] = canon; used.add(h); break
    return out


def _parse_map_overrides(pairs) -> dict:
    out = {}
    for raw in pairs or ():
        if "=" not in raw: continue
        k, _, v = raw.partition("=")
        k, v = k.strip(), v.strip()
        if k and v: out[k] = v
    return out


# ── row → Huntova lead dict ──────────────────────────────────────────

def _normalise_row(row: dict, mapping: dict) -> dict:
    """Turn a CSV row into a Huntova lead dict using `mapping`
    (csv_header → canonical_field). Synthesises contact_name from
    first/last when absent and clamps fit_score to 0-10."""
    lead = {}
    for header, val in row.items():
        canon = mapping.get(header)
        if not canon: continue
        s = (val or "").strip()
        if not s: continue
        lead[canon] = s
    if not lead.get("contact_name"):
        fn, ln = lead.get("first_name", ""), lead.get("last_name", "")
        if fn or ln:
            lead["contact_name"] = (fn + " " + ln).strip()
    fs = lead.get("fit_score")
    if fs:
        try: lead["fit_score"] = max(0, min(10, int(float(fs))))
        except (TypeError, ValueError): lead.pop("fit_score", None)
    return lead


def _make_lead_id(lead: dict) -> str:
    """Stable 12-char SHA256-hex id derived from website-or-email-or-name."""
    site = (lead.get("org_website") or "").strip().lower()
    email = (lead.get("contact_email") or "").strip().lower()
    org = (lead.get("org_name") or "").strip().lower()
    seed = site or email or org or repr(sorted(lead.items()))
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


def _dedup_keys(lead: dict) -> tuple:
    site = (lead.get("org_website") or "").strip().lower() \
        .replace("https://", "").replace("http://", "").rstrip("/")
    email = (lead.get("contact_email") or "").strip().lower()
    return (site, email)


# ── core import driver ───────────────────────────────────────────────

def _open_csv(path: str):
    # utf-8-sig handles BOM from Excel/Sheets exports.
    return open(path, "r", encoding="utf-8-sig", newline="")


def _import(user_id: int, args: argparse.Namespace,
            source: str, base_map: dict | None) -> int:
    """Generic driver: opens CSV, builds mapping, walks rows, calls upsert."""
    import asyncio as _asyncio
    import db as _db
    path = args.path
    overrides = _parse_map_overrides(getattr(args, "map", None))
    try:
        fh = _open_csv(path)
    except OSError as e:
        print(f"[huntova] cannot open {path}: {e}", file=sys.stderr)
        return 1
    with fh:
        reader = csv.DictReader(fh)
        headers = reader.fieldnames or []
        if not headers:
            print(f"[huntova] {path}: no header row", file=sys.stderr)
            return 1
        mapping = dict(base_map or {})
        if not mapping:
            mapping = _autodetect(headers)
        else:
            # Fill any unmapped headers via heuristic so partial source-maps
            # still benefit from auto-detection on extra columns.
            for h, canon in _autodetect(headers).items():
                mapping.setdefault(h, canon)
        mapping.update(overrides)
        # Drop bogus mappings to non-canonical fields (typo guard).
        mapping = {h: c for h, c in mapping.items() if c in _LEAD_FIELDS}

        if args.dry_run:
            return _print_preview(path, headers, mapping, reader, source)

        # Single asyncio loop wraps the entire import. Previous version
        # spawned a fresh loop + DB pool checkout PER ROW — for a 50k
        # row Apollo export that's 50k loop creations and pool churn,
        # which on PostgreSQL with maxconn=10 starves any concurrent
        # agent thread on `get_conn`. Bug-hunter round-5 finding #4.
        async def _run_import() -> tuple[int, int, int]:
            existing = await _db.get_leads(user_id)
            seen: set = set()
            for l in existing:
                k = _dedup_keys(l)
                if any(k): seen.add(k)
            del existing  # release before the per-row loop
            imp = skp = err = 0
            for i, row in enumerate(reader, start=1):
                lead = _normalise_row(row, mapping)
                if not lead.get("org_name") and not lead.get("org_website") \
                        and not lead.get("contact_email"):
                    skp += 1; continue
                key = _dedup_keys(lead)
                if not args.force and any(key) and key in seen:
                    skp += 1; continue
                lead.setdefault("found_date", datetime.now(timezone.utc).isoformat())
                lead.setdefault("source", f"migrate:{source}")
                lid = _make_lead_id(lead)
                lead["lead_id"] = lid
                try:
                    await _db.upsert_lead(user_id, lid, lead)
                except Exception as e:  # pragma: no cover
                    err += 1
                    if err <= 5:
                        print(f"[huntova] row {i}: {e}", file=sys.stderr)
                    continue
                if any(key): seen.add(key)
                imp += 1
                if imp % 50 == 0:
                    print(f"  {_dim(f'... {imp} imported, {skp} skipped')}")
            return imp, skp, err

        imported, skipped, errors = _asyncio.run(_run_import())
        msg = (f"\n{_bold('Imported')} {_green(str(imported))} leads "
               f"({_yellow(str(skipped))} skipped as duplicates) from {source}")
        if errors:
            msg += f" {_red(f'[{errors} errors]')}"
        print(msg + "\n")
        return 0 if errors == 0 else 2


def _print_preview(path, headers, mapping, reader, source) -> int:
    """Stream-friendly dry-run preview.

    Was `rows = list(reader)` — slurped the entire CSV into RAM. A
    user dry-running a 500MB Apollo export OOMed the CLI before
    seeing any output. Now we read only the first row for the sample
    and a streaming count for the rest. Bug-hunter round-5 finding #3.
    """
    sample_row = next(reader, None)
    rest_count = sum(1 for _ in reader) if sample_row is not None else 0
    row_count = (1 if sample_row is not None else 0) + rest_count
    print(f"\n{_bold(f'Dry-run preview — {source}')}  {_dim(path)}")
    print(f"  rows               {_green(str(row_count))}")
    print(f"  headers            {len(headers)}")
    print(f"\n  {_bold('Column mapping')}")
    if not mapping:
        print(f"    {_dim('(none detected — supply --map csv_col=lead_field)')}")
    for h in headers:
        canon = mapping.get(h)
        if canon:
            print(f"    {_cyan(h[:28]):<38} → {_green(canon)}")
        else:
            print(f"    {_dim(h[:28]):<38} → {_dim('(dropped)')}")
    if sample_row is not None:
        sample = _normalise_row(sample_row, mapping)
        print(f"\n  {_bold('Sample row →')}")
        for k, v in sample.items():
            print(f"    {_cyan(k):<22} {str(v)[:60]}")
    print("")
    return 0


# ── public dispatcher + argparse wiring ──────────────────────────────

_SOURCE_MAPS = {
    "from-csv":    ("generic", None),
    "from-apollo": ("apollo",  APOLLO_MAP),
    "from-clay":   ("clay",    CLAY_MAP),
    "from-hunter": ("hunter",  HUNTER_MAP),
}


def cmd_migrate(args: argparse.Namespace) -> int:
    """Dispatcher: huntova migrate {from-csv|from-apollo|from-clay|from-hunter|stats}."""
    from cli import _bootstrap_local_env
    user_id = _bootstrap_local_env()
    if user_id is None:
        return 1
    sub = (getattr(args, "migrate_cmd", None) or "").strip()
    if sub == "stats":
        args.dry_run = True
        return _import(user_id, args, "generic", None)
    if sub in _SOURCE_MAPS:
        source, mp = _SOURCE_MAPS[sub]
        return _import(user_id, args, source, mp)
    print("[huntova] usage: huntova migrate "
          "{from-csv|from-apollo|from-clay|from-hunter|stats} <path>",
          file=sys.stderr)
    return 1


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("path", help="Path to the CSV file to import")
    p.add_argument("--dry-run", action="store_true",
                   help="Preview rows + mapping without writing")
    p.add_argument("--force", action="store_true",
                   help="Upsert even if a row already exists (overwrites data)")
    p.add_argument("--map", action="append", default=[],
                   metavar="csv_col=lead_field",
                   help="Manual column mapping override (repeatable)")


def register(sub) -> None:
    """Attach the `migrate` subparser to cli.py's argparse tree."""
    m = sub.add_parser("migrate",
        help="Import leads from another lead-gen system (Apollo/Clay/Hunter/CSV)",
        description="Bulk-import leads from CSV exports. "
                    "Pattern adapted from OpenClaw `openclaw migrate`.")
    m_sub = m.add_subparsers(dest="migrate_cmd")
    g = m_sub.add_parser("from-csv", help="Generic CSV import (auto-detect columns)")
    _add_common(g); g.set_defaults(func=cmd_migrate, migrate_cmd="from-csv")
    a = m_sub.add_parser("from-apollo", help="Apollo CSV export")
    _add_common(a); a.set_defaults(func=cmd_migrate, migrate_cmd="from-apollo")
    c = m_sub.add_parser("from-clay", help="Clay export")
    _add_common(c); c.set_defaults(func=cmd_migrate, migrate_cmd="from-clay")
    h = m_sub.add_parser("from-hunter", help="Hunter.io email-finder export")
    _add_common(h); h.set_defaults(func=cmd_migrate, migrate_cmd="from-hunter")
    s = m_sub.add_parser("stats", help="Dry-run: row count + detected columns")
    s.add_argument("path", help="Path to the CSV file to inspect")
    s.add_argument("--map", action="append", default=[],
                   metavar="csv_col=lead_field",
                   help="Manual column mapping override (repeatable)")
    s.set_defaults(func=cmd_migrate, migrate_cmd="stats", dry_run=True, force=False)
    # Bare `huntova migrate` prints usage. cmd_migrate needs these attrs.
    m.set_defaults(func=cmd_migrate, migrate_cmd="",
                   path="", dry_run=False, force=False, map=[])
