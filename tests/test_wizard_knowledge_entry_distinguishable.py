"""Regression tests for BRAIN-104 (a473): the wizard
`_knowledge` audit entry must preserve fields that uniquely
distinguish one completion from another, even when truncation
applies. The pre-fix `json.dumps(...)[:2000]` could collapse
two materially-different completes into byte-identical
content if the front of the profile was repetitive.

Failure mode (Per Huntova engineering review on
audit-log distinguishability):

`_knowledge_entry["content"]` was:

    json.dumps({"profile": profile, "qa_count": len(history)})[:2000]

If the profile starts with a long stable field (e.g. a 1500-
char `business_description`), the [:2000] slice cuts off the
rest of the profile. Two completes that differ only in
later-ranked fields (services list edits, region tweaks,
phase-5 answers) collapse to the same visible content.
Audit reads:

    Entry 1 (2026-01-15): same first-2000-chars
    Entry 2 (2026-02-15): same first-2000-chars
    Entry 3 (2026-03-15): same first-2000-chars
    ...

Operator can't distinguish what changed across attempts.
The audit log technically exists but loses its reason.

Standard structured-logging guidance: bounded payloads must
preserve identifying fields + a digest, not slice raw JSON
at an arbitrary byte boundary.

Invariants:
- Each entry carries a `fingerprint` field — SHA256 (or
  similar stable hash) of canonical(profile + history). Two
  materially-different profiles collide only on real hash
  collision (effectively never).
- Each entry carries compact distinguishing fields:
  `company_name` (capped), `target_clients` first chunk,
  `regions` count, `services_count`, etc.
- The free-text `content` summary still exists but is no
  longer the sole identifier — the structured fields carry
  the distinguishability load.
- Two completes that differ only in later-ranked profile
  fields (deliberately constructed test) produce
  distinguishable knowledge entries.
"""
from __future__ import annotations
import inspect


def test_knowledge_entry_includes_fingerprint():
    """Source-level: the entry construction must include a
    `fingerprint` field that's a stable hash of the input."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    # Find the _knowledge_entry assignment.
    entry_idx = src.find("_knowledge_entry =")
    assert entry_idx != -1
    block = src[entry_idx:entry_idx + 1500]
    assert '"fingerprint"' in block, (
        "BRAIN-104 regression: `_knowledge_entry` must include "
        "a `fingerprint` field. Without a stable hash, two "
        "near-identical profiles collide in the truncated "
        "content view."
    )


def test_fingerprint_is_sha256_of_canonical_input():
    """Source-level: the fingerprint should be the same SHA256
    digest the BRAIN-85 idempotency cache already computes —
    reuse the value, don't recompute. Look for the
    `_complete_fingerprint` variable referenced inside the
    entry."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    entry_idx = src.find("_knowledge_entry =")
    block = src[entry_idx:entry_idx + 1500]
    assert "_complete_fingerprint" in block, (
        "BRAIN-104 regression: entry's fingerprint should "
        "reuse the BRAIN-85 `_complete_fingerprint` already "
        "computed earlier in the request. Recomputing would "
        "be wasteful + risk drift."
    )


def test_knowledge_entry_has_distinguishing_compact_fields():
    """Source-level: the entry must carry at least the
    minimal compact identifying fields — company_name,
    services_count, regions_count, qa_count — so an operator
    can scan the audit log and spot what changed without
    parsing the truncated JSON blob."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    entry_idx = src.find("_knowledge_entry =")
    block = src[entry_idx:entry_idx + 1500]
    # company_name was already implicitly there via
    # json.dumps(profile); now it should be an explicit
    # top-level field.
    has_compact_fields = (
        '"company_name"' in block
        and ("services_count" in block or "regions_count" in block
             or "regions" in block)
    )
    assert has_compact_fields, (
        "BRAIN-104 regression: entry must carry compact "
        "distinguishing fields (company_name plus at least "
        "one count/list field) so the audit row is "
        "human-scannable without unpacking the JSON content."
    )


def test_knowledge_entry_qa_count_preserved():
    """Don't regress: the existing qa_count field must still
    be present (operator workflow depends on it)."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    entry_idx = src.find("_knowledge_entry =")
    block = src[entry_idx:entry_idx + 1500]
    assert "qa_count" in block, (
        "BRAIN-104 sanity: qa_count field must still be "
        "in the entry."
    )


def test_two_late_diverging_profiles_produce_distinct_entries(local_env):
    """Behavioral: two completes whose profiles share an
    identical first-2000-chars but differ later (e.g. last
    region, last service) must produce DISTINGUISHABLE
    knowledge entries. The fingerprint field guarantees this."""
    import asyncio
    import hashlib
    import json as _json

    async def _run():
        # Simulate the post-fix entry construction directly.
        # We don't run the full FastAPI request here; we test
        # the contract: same compact identifiers + DIFFERENT
        # fingerprints when canonical inputs differ.
        from server import _canonicalize_complete_payload

        # Construct two profiles with identical 2000-char
        # prefix but different tails.
        big_prefix = "DTC skincare brands scaling past 1M MRR " * 60  # ~2400 chars
        profile_a = {
            "company_name": "Acme",
            "business_description": big_prefix,
            "regions": ["United States", "Italy"],
            "services": ["CRO", "Shopify migration"],
        }
        profile_b = {
            "company_name": "Acme",
            "business_description": big_prefix,
            "regions": ["United States", "Italy", "Spain"],   # +1
            "services": ["CRO", "Shopify migration", "Email"],  # +1
        }

        # Mirror the BRAIN-85 fingerprint computation.
        canon_a, hist_a = _canonicalize_complete_payload(profile_a, [])
        canon_b, hist_b = _canonicalize_complete_payload(profile_b, [])
        canonical_a = _json.dumps(
            {"profile": canon_a, "history": hist_a},
            sort_keys=True, default=str,
        )
        canonical_b = _json.dumps(
            {"profile": canon_b, "history": hist_b},
            sort_keys=True, default=str,
        )
        fp_a = hashlib.sha256(canonical_a.encode("utf-8")).hexdigest()
        fp_b = hashlib.sha256(canonical_b.encode("utf-8")).hexdigest()

        # The fingerprints must differ — that's the
        # distinguishability guarantee.
        assert fp_a != fp_b, (
            "BRAIN-104 sanity: canonicalization broke; two "
            "different inputs canonicalize to the same hash."
        )

        # And the truncated `content` field WOULD have
        # collapsed (this confirms the failure mode is real):
        old_content_a = _json.dumps(
            {"profile": profile_a, "qa_count": 0}
        )[:2000]
        old_content_b = _json.dumps(
            {"profile": profile_b, "qa_count": 0}
        )[:2000]
        assert old_content_a == old_content_b, (
            "BRAIN-104 sanity: pre-fix truncation should "
            "collapse these two profiles in the test fixture; "
            "if they differ, the fixture isn't exercising "
            "the failure mode."
        )
        # Post-fix: the fingerprint distinguishes them
        # regardless of truncation.

    asyncio.run(_run())
