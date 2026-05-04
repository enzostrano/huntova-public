"""Regression tests for the a1130 GDPR + data-retention audit
(GDPR-PURGE-1/2/3).

Failure modes pinned:

GDPR-PURGE-1: `app.purge_expired_leads()` previously only ran inside
the `if __name__ == '__main__'` branch of app.py, so under
`huntova serve` (the only entry path real users take) it never
fired. Privacy page promised 2-year retention; reality was infinite
retention. server.py now wires a `_gdpr_retention_loop` task on
startup that runs the purge at boot + every 24h.

GDPR-PURGE-1b: the cutoff comparison was lexicographic string
compare on raw `found_date` ISO strings. Naive vs tz-aware ISO
strings sort inconsistently (the `+00:00` suffix tilts the order
even when the underlying instant matches), so naive timestamps
were silently classified as expired. Replaced with an explicit
`_parse_iso_utc` parse + datetime compare.

GDPR-PURGE-1c: archive purge `(archived_date or found_date or "") >
cutoff` did the OPPOSITE of the active-leads branch — empty string
is never > cutoff, so a missing date meant DELETE in archive but
KEEP in active. Aligned: unknown date ⇒ keep.

GDPR-PURGE-2: `app.gdpr_erasure` and `db.gdpr_erasure` had multiple
data-leak paths:
  - Domain erasure ignored `contact_email` and `_all_emails_found`
    on the target domain.
  - Cloud db.gdpr_erasure didn't wipe `seen_fingerprints` rows even
    though under `lead_dedupe_key=email` those rows store the raw
    email plaintext.
  - Wizard data scrub for free-text fields the user might have
    pasted contact info into.

GDPR-PURGE-3: no audit trail of erasures. Privacy page promised
"Audit trail — full logging of data operations" but no row was
written. Added local-mode `gdpr_audit.json` + cloud
`admin_audit_log` row with hashed identifier (so the audit trail
isn't itself a cache of erased PII).
"""
from __future__ import annotations

import inspect
import json
import os
from datetime import datetime, timedelta, timezone

import pytest


# ────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────

def _isolate_app_dirs(tmp_path, monkeypatch):
    """Point app.py's BASE_DIR + JSON paths at a tmp dir for the
    duration of one test. Avoids reload (slow + fragile) by
    rebinding the module-level constants directly."""
    import app
    base = tmp_path / "huntova_data"
    base.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(app, "BASE_DIR", str(base), raising=True)
    monkeypatch.setattr(app, "MASTER_LEADS_JSON",
                        str(base / "master_leads.json"), raising=True)
    monkeypatch.setattr(app, "ARCHIVED_JSON",
                        str(base / "archived_leads.json"), raising=True)
    monkeypatch.setattr(app, "SEEN_HISTORY_JSON",
                        str(base / "seen_history.json"), raising=True)
    monkeypatch.setattr(app, "SETTINGS_JSON",
                        str(base / "settings.json"), raising=True)
    monkeypatch.setattr(app, "GDPR_AUDIT_JSON",
                        str(base / "gdpr_audit.json"), raising=True)
    return base


# ────────────────────────────────────────────────────────────────
# GDPR-PURGE-1: purge_expired_leads is wired into the running server
# ────────────────────────────────────────────────────────────────

def test_purge_expired_leads_wired_into_server_startup():
    """server.py must launch a task that calls purge_expired_leads;
    pre-fix the function lived only in app.py's __main__ block which
    never executes under `huntova serve`."""
    import server
    src = inspect.getsource(server)
    assert "_gdpr_retention_loop" in src, (
        "GDPR-PURGE-1 regression: server.py must define a periodic "
        "GDPR retention loop. Without it, purge_expired_leads is "
        "dead code under `huntova serve` and the privacy page's "
        "2-year retention promise is unenforced."
    )
    assert "purge_expired_leads" in src, (
        "GDPR-PURGE-1 regression: server.py must invoke "
        "purge_expired_leads from somewhere on the running event "
        "loop, not just rely on app.py's __main__ block."
    )
    assert "gdpr_retention_task" in src, (
        "GDPR-PURGE-1 regression: the retention loop task must be "
        "stashed on app.state to keep a strong ref (otherwise the "
        "weak-ref task can be GC'd between tick boundaries — same "
        "long-tail bug #42 dance the session_cleanup_loop already "
        "had to fix)."
    )


# ────────────────────────────────────────────────────────────────
# GDPR-PURGE-1b: date compare is parsed, not stringly typed
# ────────────────────────────────────────────────────────────────

def test_parse_iso_utc_helper_exists():
    """Centralised tz-aware parser; gives `>` compares a datetime
    not a string."""
    import app
    assert hasattr(app, "_parse_iso_utc"), (
        "GDPR-PURGE-1b regression: _parse_iso_utc helper missing. "
        "Without a centralised parser the purge falls back to raw "
        "string compare and naive timestamps drop silently."
    )
    f = app._parse_iso_utc
    assert f("2024-01-01T00:00:00") is not None
    assert f("2024-01-01T00:00:00").tzinfo is not None, (
        "Naive timestamps must be coerced to UTC, not returned naive — "
        "otherwise downstream `>` compares with tz-aware cutoff "
        "raise TypeError."
    )
    assert f("2024-01-01T00:00:00Z") is not None
    assert f("2024-01-01T00:00:00+00:00") is not None
    assert f("") is None and f(None) is None and f("garbage") is None


def test_purge_expired_leads_keeps_naive_iso_recent_lead(tmp_path, monkeypatch):
    """Lead with a NAIVE ISO `found_date` of yesterday must NOT get
    purged. Pre-fix lexicographic compare made naive sort inconsistent
    with the tz-aware cutoff and the lead got dropped."""
    import app
    base = _isolate_app_dirs(tmp_path, monkeypatch)
    yesterday_naive = (datetime.now(timezone.utc) - timedelta(days=1)
                       ).replace(tzinfo=None).isoformat()
    leads = [{"lead_id": "x1", "org_name": "Acme",
              "found_date": yesterday_naive,
              "contact_email": "ceo@acme.example"}]
    app._atomic_write(app.MASTER_LEADS_JSON, leads)

    app.purge_expired_leads()

    survivors = json.loads(open(app.MASTER_LEADS_JSON).read())
    assert len(survivors) == 1, (
        "GDPR-PURGE-1b regression: a recent lead with a naive ISO "
        "found_date got purged. Pre-fix string compare classified "
        "naive timestamps as expired."
    )


def test_purge_expired_leads_drops_truly_expired(tmp_path, monkeypatch):
    """Lead older than DATA_RETENTION_DAYS must be deleted."""
    import app
    base = _isolate_app_dirs(tmp_path, monkeypatch)
    very_old = (datetime.now(timezone.utc)
                - timedelta(days=app.DATA_RETENTION_DAYS + 30)).isoformat()
    leads = [
        {"lead_id": "old", "org_name": "Old", "found_date": very_old},
        {"lead_id": "new", "org_name": "New",
         "found_date": datetime.now(timezone.utc).isoformat()},
    ]
    app._atomic_write(app.MASTER_LEADS_JSON, leads)

    app.purge_expired_leads()

    survivors = json.loads(open(app.MASTER_LEADS_JSON).read())
    assert {l["lead_id"] for l in survivors} == {"new"}, (
        "GDPR-PURGE-1 regression: expired leads must actually get "
        "purged — the retention promise is meaningless if old leads "
        "linger forever."
    )


def test_purge_expired_leads_keeps_lead_without_date(tmp_path, monkeypatch):
    """Lead with no parseable date must be kept (we can't prove
    it's expired). Pre-fix the active-leads branch already did this
    via `not l.get('found_date')` but the archive branch did the
    opposite."""
    import app
    _isolate_app_dirs(tmp_path, monkeypatch)
    leads = [
        {"lead_id": "no_date", "org_name": "X", "found_date": ""},
        {"lead_id": "garbage_date", "org_name": "Y",
         "found_date": "not-a-date"},
        {"lead_id": "missing", "org_name": "Z"},
    ]
    app._atomic_write(app.MASTER_LEADS_JSON, leads)

    app.purge_expired_leads()

    survivors = json.loads(open(app.MASTER_LEADS_JSON).read())
    assert len(survivors) == 3, (
        "GDPR-PURGE-1c regression: leads with missing/unparseable "
        "found_date must be retained — purging on uncertainty is "
        "data loss."
    )


def test_purge_expired_archive_branch_aligned_with_active(tmp_path, monkeypatch):
    """Pre-fix the archive purge used `(archived_date or found_date
    or '') > cutoff`. Empty-string-vs-cutoff is always False, so an
    archived lead with no date got DELETED. Active branch used `not
    l.get('found_date') or ...` which KEPT them. Two opposite policies
    on the same compliance question — clearly a bug."""
    import app
    _isolate_app_dirs(tmp_path, monkeypatch)
    archived = [
        {"lead_id": "no_date_archived", "org_name": "X"},
        {"lead_id": "ancient", "org_name": "Y",
         "archived_date": (datetime.now(timezone.utc)
                           - timedelta(days=app.DATA_RETENTION_DAYS + 5)
                           ).isoformat()},
        {"lead_id": "recent_archived", "org_name": "Z",
         "archived_date": datetime.now(timezone.utc).isoformat()},
    ]
    app._atomic_write(app.ARCHIVED_JSON, archived)
    app._atomic_write(app.MASTER_LEADS_JSON, [])  # no active leads

    app.purge_expired_leads()

    survivors = json.loads(open(app.ARCHIVED_JSON).read())
    survivor_ids = {l["lead_id"] for l in survivors}
    assert "ancient" not in survivor_ids, (
        "Archive purge must still drop genuinely-old archived leads."
    )
    assert "recent_archived" in survivor_ids
    assert "no_date_archived" in survivor_ids, (
        "GDPR-PURGE-1c regression: archive branch deleted leads with "
        "no parseable date, while active-leads branch kept them. "
        "Two opposite policies — bug."
    )


# ────────────────────────────────────────────────────────────────
# GDPR-PURGE-2: erasure scrubs everywhere PII could hide
# ────────────────────────────────────────────────────────────────

def test_gdpr_erasure_email_path_unchanged(tmp_path, monkeypatch):
    """Sanity: email-mode erasure still removes leads whose
    contact_email matches."""
    import app
    _isolate_app_dirs(tmp_path, monkeypatch)
    app._atomic_write(app.MASTER_LEADS_JSON, [
        {"lead_id": "a", "contact_email": "ceo@acme.example",
         "org_name": "Acme"},
        {"lead_id": "b", "contact_email": "ceo@beta.example",
         "org_name": "Beta"},
    ])
    res = app.gdpr_erasure("CEO@acme.example")
    survivors = json.loads(open(app.MASTER_LEADS_JSON).read())
    assert {l["lead_id"] for l in survivors} == {"b"}
    assert res["deleted"] == 1


def test_gdpr_erasure_domain_path_scrubs_contact_email(tmp_path, monkeypatch):
    """GDPR-PURGE-2 fix: domain-mode erasure must also delete leads
    where `contact_email` is on the target domain even if
    `org_website` is on a different domain (e.g. a referral lead)."""
    import app
    _isolate_app_dirs(tmp_path, monkeypatch)
    app._atomic_write(app.MASTER_LEADS_JSON, [
        # contact email on target domain, org_website elsewhere
        {"lead_id": "leak", "org_website": "https://referrer.example",
         "contact_email": "ceo@target.example"},
        # contact email + org_website both elsewhere
        {"lead_id": "safe", "org_website": "https://other.example",
         "contact_email": "x@other.example"},
        # org_website on target — pre-fix already handled
        {"lead_id": "ok", "org_website": "https://target.example",
         "contact_email": "x@x.example"},
    ])
    app.gdpr_erasure("target.example")
    survivors = json.loads(open(app.MASTER_LEADS_JSON).read())
    assert {l["lead_id"] for l in survivors} == {"safe"}, (
        "GDPR-PURGE-2 regression: domain erasure must scrub leads "
        "whose contact_email or _all_emails_found contains the "
        "target domain — not just leads whose org_website netloc "
        "matches. Otherwise referral leads keep the data subject's "
        "email after a domain-erasure request."
    )


def test_gdpr_erasure_domain_path_scrubs_all_emails_found(tmp_path, monkeypatch):
    """`_all_emails_found` is a list of harvested emails. Domain
    erasure must check it too."""
    import app
    _isolate_app_dirs(tmp_path, monkeypatch)
    app._atomic_write(app.MASTER_LEADS_JSON, [
        {"lead_id": "harvest", "org_website": "https://other.example",
         "contact_email": "primary@other.example",
         "_all_emails_found": ["primary@other.example",
                                "secondary@target.example"]},
        {"lead_id": "clean", "org_website": "https://other.example",
         "contact_email": "primary@other.example",
         "_all_emails_found": ["primary@other.example"]},
    ])
    app.gdpr_erasure("target.example")
    survivors = json.loads(open(app.MASTER_LEADS_JSON).read())
    assert {l["lead_id"] for l in survivors} == {"clean"}


def test_gdpr_erasure_writes_local_audit_row(tmp_path, monkeypatch):
    """GDPR-PURGE-3: local-mode erasure must append an audit row to
    gdpr_audit.json so the privacy-page audit-trail promise is
    actually fulfilled. Identifier is hashed so the audit log isn't
    itself a copy of erased PII."""
    import app
    _isolate_app_dirs(tmp_path, monkeypatch)
    app._atomic_write(app.MASTER_LEADS_JSON, [
        {"lead_id": "x", "contact_email": "subject@erase.example"},
    ])
    app.gdpr_erasure("subject@erase.example")
    rows = json.loads(open(app.GDPR_AUDIT_JSON).read())
    assert len(rows) == 1
    row = rows[0]
    assert row["action"] == "erasure_email"
    # Identifier MUST be hashed, never stored verbatim.
    assert "subject@erase.example" not in json.dumps(row), (
        "GDPR-PURGE-3 regression: audit row stored the identifier "
        "verbatim. The audit log itself becomes a cache of erased "
        "personal data — exactly what the regulator audits next."
    )
    assert row["deleted"] == 1


def test_gdpr_erasure_redacts_wizard_freetext(tmp_path, monkeypatch):
    """If the user pasted a contact name/email into a wizard field,
    erasure must scrub the wizard too — otherwise the agent regrows
    leads from the same seed. Redact-in-place keeps the wizard
    functional vs nuking it wholesale."""
    import app
    _isolate_app_dirs(tmp_path, monkeypatch)
    # Stage settings with a wizard mentioning the target.
    app._atomic_write(app.SETTINGS_JSON, {
        "wizard": {
            "icp_description": "We sell to Subject@Erase.example and others",
            "lookalikes": "Like subject@erase.example",
            "red_flags": ["mentions subject@erase.example"],
        },
    })
    app._atomic_write(app.MASTER_LEADS_JSON, [])

    res = app.gdpr_erasure("subject@erase.example")
    settings = json.loads(open(app.SETTINGS_JSON).read())
    wiz = settings["wizard"]
    for v in [wiz["icp_description"], wiz["lookalikes"]] + wiz["red_flags"]:
        assert "subject@erase.example" not in v.lower(), (
            "GDPR-PURGE-2 regression: wizard free-text fields still "
            "contain the erased identifier. Erasure must scrub user-"
            "authored copies of personal data, not just leads."
        )
    assert res.get("wizard_redactions", 0) >= 2


# ────────────────────────────────────────────────────────────────
# GDPR-PURGE-2 (cloud parity)
# ────────────────────────────────────────────────────────────────

def test_db_gdpr_erasure_checks_all_emails_and_email_domain():
    """Source-level cloud parity: db.gdpr_erasure must consult
    `_all_emails_found` AND match contact_email by domain on
    domain-erasure path."""
    import db
    src = inspect.getsource(db.gdpr_erasure)
    assert "_all_emails_found" in src, (
        "GDPR-PURGE-2 regression (cloud): db.gdpr_erasure ignored "
        "the harvested-emails field, leaking the data subject's "
        "secondary occurrences."
    )
    assert "contact_email" in src and "split" in src, (
        "GDPR-PURGE-2 regression (cloud): domain-mode erasure must "
        "match the email-domain of contact_email, not just netloc "
        "of org_website/url."
    )


def test_db_gdpr_erasure_wipes_seen_fingerprints():
    """When `lead_dedupe_key=email` the seen_fingerprints table
    stores the raw email plaintext (`email:foo@bar.com`). Erasure
    must wipe matching rows — otherwise re-discovering the same
    lead repopulates the table from search results."""
    import db
    src = inspect.getsource(db.gdpr_erasure)
    assert "seen_fingerprints" in src and "DELETE" in src, (
        "GDPR-PURGE-2 regression (cloud): db.gdpr_erasure didn't "
        "scrub seen_fingerprints. Under email-mode dedupe those rows "
        "are themselves a copy of the personal data the user wants "
        "erased."
    )


def test_db_gdpr_erasure_writes_admin_audit_row():
    """Cloud audit trail uses the existing admin_audit_log table.
    Self-erasure rows have admin_user_id == target_user_id."""
    import db
    src = inspect.getsource(db.gdpr_erasure)
    assert "log_admin_action" in src, (
        "GDPR-PURGE-3 regression (cloud): no audit trail row for "
        "GDPR erasures. Privacy page promises an audit trail; without "
        "writing to admin_audit_log we can't demonstrate compliance."
    )
    assert "identifier_hash" in src or "hashlib" in src, (
        "GDPR-PURGE-3 regression: audit row must store a HASH of "
        "the identifier, not the raw value — otherwise the audit "
        "log becomes a cache of erased personal data."
    )
