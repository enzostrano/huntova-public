"""huntova doctor --email — deliverability pre-flight.

Checks the sender domain for the three records mailbox providers use
to gate inbox placement (SPF, DKIM, DMARC) and verifies that recent
recipient domains have working MX records. Print a red/amber/green
matrix so the user sees which problem to fix first.

Lazy-imports `dnspython` (not in base deps). Falls back to a
"dnspython not installed" message rather than crashing the doctor.
"""

from __future__ import annotations

import sys


def _green(s: str) -> str: return f"\033[32m{s}\033[0m"
def _red(s: str) -> str: return f"\033[31m{s}\033[0m"
def _yellow(s: str) -> str: return f"\033[33m{s}\033[0m"
def _bold(s: str) -> str: return f"\033[1m{s}\033[0m"
def _dim(s: str) -> str: return f"\033[2m{s}\033[0m"


def _try_import_dns():
    try:
        import dns.resolver  # type: ignore
        import dns.exception  # type: ignore
        return dns
    except ImportError:
        return None


def _query_txt(resolver, name: str) -> list[str]:
    """Return raw TXT strings for `name`, or empty list if not found."""
    try:
        ans = resolver.resolve(name, "TXT", lifetime=4.0)
    except Exception:
        return []
    out: list[str] = []
    for rr in ans:
        try:
            # rdata.strings is bytes, may be split into chunks for >255-char records
            out.append(b"".join(rr.strings).decode("utf-8", "replace"))
        except Exception:
            continue
    return out


def _query_mx(resolver, name: str) -> list[str]:
    try:
        ans = resolver.resolve(name, "MX", lifetime=4.0)
    except Exception:
        return []
    return [str(rr.exchange).rstrip(".") for rr in ans]


def _check_spf(records: list[str]) -> tuple[str, str]:
    spf_lines = [r for r in records if r.lower().startswith("v=spf1")]
    if not spf_lines:
        return "fail", "no SPF record (`v=spf1 …` TXT)"
    if len(spf_lines) > 1:
        return "fail", f"multiple SPF records ({len(spf_lines)}); RFC 7208 forbids it"
    spf = spf_lines[0].lower()
    if "+all" in spf:
        return "warn", "SPF ends in `+all` — accepts mail from anywhere"
    if "?all" in spf:
        return "warn", "SPF ends in `?all` — neutral; mailbox providers won't trust it"
    if "-all" not in spf and "~all" not in spf:
        return "warn", "SPF doesn't end in `-all` or `~all` — soft policy unknown"
    return "ok", spf_lines[0][:120]


def _check_dmarc(records: list[str]) -> tuple[str, str]:
    dmarc_lines = [r for r in records if r.lower().startswith("v=dmarc1")]
    if not dmarc_lines:
        return "fail", "no DMARC record at `_dmarc.<domain>` (`v=DMARC1 …` TXT)"
    line = dmarc_lines[0].lower()
    if "p=none" in line:
        return "warn", "DMARC `p=none` — reporting only, no enforcement"
    if "p=reject" in line:
        return "ok", dmarc_lines[0][:120]
    if "p=quarantine" in line:
        return "warn", "DMARC `p=quarantine` — soft policy (junk folder, not reject)"
    return "warn", "DMARC policy not parseable"


def _check_dkim(resolver, domain: str, selectors: list[str]) -> tuple[str, str]:
    """DKIM selectors are vendor-specific; we probe the most common
    ones (`default`, `google`, `selector1` for M365, `mailgun`, etc.)
    and report any one hit as ok."""
    found: list[str] = []
    for sel in selectors:
        recs = _query_txt(resolver, f"{sel}._domainkey.{domain}")
        for r in recs:
            if r.lower().startswith("v=dkim1"):
                found.append(sel)
                break
    if not found:
        return "warn", (f"no DKIM TXT at any of {', '.join(selectors)}._domainkey.{domain} — "
                         "set DKIM with your ESP")
    return "ok", f"DKIM selector(s): {', '.join(found)}"


def _check_mx(resolver, domain: str) -> tuple[str, str]:
    if not domain:
        return "fail", "(no domain)"
    mx = _query_mx(resolver, domain)
    if not mx:
        return "fail", f"no MX records — recipient mail server unreachable"
    return "ok", f"MX: {', '.join(mx[:3])}"


def _print_row(status: str, label: str, msg: str) -> None:
    if status == "ok":
        marker = _green("✓")
    elif status == "warn":
        marker = _yellow("!")
    else:
        marker = _red("✗")
    print(f"  {marker} {label:<10s} {_dim(msg)}")


# ── public entry point ─────────────────────────────────────────────

def run_email_doctor(sender_email: str | None = None,
                     recipient_domains: list[str] | None = None,
                     selectors: list[str] | None = None,
                     verbose: bool = False) -> int:
    """Print the deliverability matrix. Returns 0 on all-green,
    non-zero if any critical check fails."""
    print(_bold("\nDeliverability pre-flight\n"))
    dns = _try_import_dns()
    if dns is None:
        print(_yellow("  ! dnspython not installed — `pip install dnspython` "
                      "(or `pipx inject huntova dnspython`) for SPF/DKIM/DMARC checks."))
        return 1
    import dns.resolver  # type: ignore

    resolver = dns.resolver.Resolver()
    resolver.lifetime = 4.0
    resolver.timeout = 4.0

    # ── sender side ────────────────────────────────────────────────
    sender_domain = ""
    if sender_email and "@" in sender_email:
        sender_domain = sender_email.split("@", 1)[-1].strip().lower()

    fail = False
    if sender_domain:
        print(_bold(f"sender: {sender_domain}"))
        spf_recs = _query_txt(resolver, sender_domain)
        st, msg = _check_spf(spf_recs)
        _print_row(st, "SPF", msg)
        if st == "fail":
            fail = True

        dmarc_recs = _query_txt(resolver, f"_dmarc.{sender_domain}")
        st, msg = _check_dmarc(dmarc_recs)
        _print_row(st, "DMARC", msg)
        if st == "fail":
            fail = True

        sels = selectors or [
            "default", "google", "selector1", "selector2",
            "mailgun", "mailjet", "k1", "k2", "smtpapi", "dkim",
            # Fastmail / Namecheap / SES / common modern providers —
            # without these the probe falsely flagged DKIM as missing
            # for users on those services.
            "fm1", "fm2", "fm3", "smtp", "amazonses", "s1", "s2",
            "protonmail", "protonmail2", "protonmail3",
        ]
        st, msg = _check_dkim(resolver, sender_domain, sels)
        _print_row(st, "DKIM", msg)

        mx = _query_mx(resolver, sender_domain)
        if mx:
            _print_row("ok", "MX", f"{', '.join(mx[:3])}")
        else:
            _print_row("warn", "MX", "no MX — sender domain can't receive bounces")
    else:
        print(_yellow("  no sender configured. Set `from_email` in Settings → Outreach"
                      " or pass --email <addr@domain>."))

    # ── recipient side ─────────────────────────────────────────────
    if recipient_domains:
        print()
        print(_bold("recent recipient domains"))
        seen: set[str] = set()
        for d in recipient_domains:
            d = (d or "").strip().lower()
            if not d or d in seen:
                continue
            seen.add(d)
            st, msg = _check_mx(resolver, d)
            _print_row(st, d, msg)
            if st == "fail":
                fail = True
            if len(seen) >= (50 if verbose else 12):
                if not verbose:
                    print(_dim(f"  … {len(recipient_domains) - len(seen)} more (use --verbose)"))
                break

    print()
    return 1 if fail else 0


def doctor_email_check(user_id: int, args) -> int:
    """Wrapper called from `huntova doctor --email`. Pulls sender +
    recent recipient domains from the local DB, then delegates to
    `run_email_doctor`."""
    import asyncio as _aio
    import db as _db
    import os as _os

    s = _aio.run(_db.get_settings(user_id)) or {}
    sender = (s.get("from_email") or _os.environ.get("SMTP_USER") or "").strip()

    leads = _aio.run(_db.get_leads(user_id, limit=200)) or []
    domains: list[str] = []
    for ld in leads:
        em = (ld.get("contact_email") or "").strip().lower()
        if em and "@" in em:
            domains.append(em.split("@", 1)[-1])
    return run_email_doctor(
        sender_email=sender,
        recipient_domains=domains,
        verbose=bool(getattr(args, "verbose", False)),
    )
