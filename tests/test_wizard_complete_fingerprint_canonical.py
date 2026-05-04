"""Regression tests for BRAIN-86 (a455): the BRAIN-85 fingerprint
cache must use a CANONICAL post-validation form, not the raw
client payload.

Failure mode (Per Huntova engineering review on idempotency-key
canonicalization):

BRAIN-85 (a454) computes
`hashlib.sha256(json.dumps({profile, history}, sort_keys=True))`
where `profile` and `history` are the post-validation outputs.
That handles unknown-key smuggling and dict-shape attacks
because the validation layer already rejected them — good.

But the cache still misses semantically-identical retries that
differ only in:

- **Whitespace**: a buggy client serializing `"Paris "` vs
  `"Paris"` vs `" Paris "` for the same `regions` entry.
- **Internal whitespace runs**: `"Series A B2B"` vs
  `"Series A  B2B"` (double space) in a description field.
- **List ordering for unordered fields**: `regions:
  ["United States", "Italy"]` vs `["Italy", "United States"]`
  represent the same business reach. With BRAIN-85 they hash
  differently.
- **Empty-vs-absent**: `outreach_tone: ""` vs the field
  missing entirely from the payload.

Each near-miss re-runs the entire BRAIN-72 brain build + DNA
generation pipeline. The user pays for the duplicate.

Idempotency-key guidance (idempotent-API patterns): the cache
key must be computed from a CANONICAL form. Whitespace
normalization, sort-stable list ordering for semantically
unordered fields, and empty-vs-absent collapsing are the
standard transformations.

Invariants:
- Helper `_canonicalize_complete_payload(profile, history)`
  exists and produces a deterministic, normalized representation.
- Each string value: `.strip()` + internal whitespace collapsed.
- Order-irrelevant list fields (regions, services, buyer_roles,
  icp_industries, exclusions, lookalikes, competitors,
  tech_stack, certifications, social_proof, languages,
  example_good_clients, example_bad_clients, lead_sources,
  triggers): sorted.
- Empty values dropped (empty string, empty list).
- History list preserves order (conversation flow is
  semantically meaningful).
- Each history item's question + answer trimmed.
- The endpoint's fingerprint computation calls the canonicalizer
  BEFORE hashing.
"""
from __future__ import annotations
import inspect


def test_canonicalize_helper_exists():
    """The canonicalizer must be a callable on `server`."""
    import server as _s
    fn = getattr(_s, "_canonicalize_complete_payload", None)
    assert fn is not None and callable(fn), (
        "BRAIN-86 regression: server must expose "
        "`_canonicalize_complete_payload(profile, history)` so the "
        "fingerprint cache hits across whitespace + list-order "
        "drift."
    )


def test_whitespace_drift_produces_same_canonical():
    """`'Paris '` and `'Paris'` and `' Paris '` must all
    canonicalize to the same value."""
    import server as _s
    fn = _s._canonicalize_complete_payload
    a = fn({"company_name": "Acme"}, [])
    b = fn({"company_name": "Acme  "}, [])
    c = fn({"company_name": " Acme"}, [])
    d = fn({"company_name": "Acme\t"}, [])
    assert a == b == c == d, (
        f"BRAIN-86 regression: whitespace-only differences must "
        f"canonicalize identically. Got a={a!r} b={b!r} c={c!r} d={d!r}."
    )


def test_list_order_drift_for_regions_produces_same_canonical():
    """`regions: [US, IT]` and `regions: [IT, US]` represent the
    same business reach and must hash identically."""
    import server as _s
    fn = _s._canonicalize_complete_payload
    a = fn({"regions": ["United States", "Italy"]}, [])
    b = fn({"regions": ["Italy", "United States"]}, [])
    assert a == b, (
        f"BRAIN-86 regression: regions list order must not affect "
        f"the canonical form. Got a={a!r} b={b!r}."
    )


def test_list_order_drift_for_services_produces_same_canonical():
    """Services is an unordered set in spirit. Order shouldn't
    affect the cache key."""
    import server as _s
    fn = _s._canonicalize_complete_payload
    a = fn({"services": ["CRO", "Shopify migration"]}, [])
    b = fn({"services": ["Shopify migration", "CRO"]}, [])
    assert a == b, (
        f"BRAIN-86 regression: services list order must not affect "
        f"canonical form."
    )


def test_empty_vs_absent_collapses():
    """A field that's missing entirely must canonicalize the
    same as a field set to `''` or `[]`."""
    import server as _s
    fn = _s._canonicalize_complete_payload
    a = fn({"company_name": "Acme"}, [])
    b = fn({"company_name": "Acme", "outreach_tone": ""}, [])
    c = fn({"company_name": "Acme", "regions": []}, [])
    assert a == b == c, (
        f"BRAIN-86 regression: empty values must drop out — "
        f"empty-vs-absent must collapse to the same canonical form."
    )


def test_history_order_is_preserved():
    """History is a conversation flow; reordering Q/A pairs
    represents a different transcript and must hash differently."""
    import server as _s
    fn = _s._canonicalize_complete_payload
    a = fn({}, [{"question": "q1", "answer": "a1"},
                {"question": "q2", "answer": "a2"}])
    b = fn({}, [{"question": "q2", "answer": "a2"},
                {"question": "q1", "answer": "a1"}])
    assert a != b, (
        "BRAIN-86 regression: history conversation order is "
        "semantically meaningful; reordered transcripts must NOT "
        "collide in the cache."
    )


def test_history_item_strings_trimmed():
    """Whitespace inside Q/A pairs must canonicalize the same."""
    import server as _s
    fn = _s._canonicalize_complete_payload
    a = fn({}, [{"question": "q1", "answer": "a1"}])
    b = fn({}, [{"question": "q1 ", "answer": " a1"}])
    assert a == b, (
        "BRAIN-86 regression: history Q/A pairs must be trimmed "
        "before hashing."
    )


def test_internal_whitespace_runs_collapse():
    """Multi-space runs inside string fields must collapse to a
    single space so `'Series A  B2B'` matches `'Series A B2B'`."""
    import server as _s
    fn = _s._canonicalize_complete_payload
    a = fn({"target_clients": "Series A B2B"}, [])
    b = fn({"target_clients": "Series A  B2B"}, [])
    c = fn({"target_clients": "Series A\tB2B"}, [])
    assert a == b == c, (
        f"BRAIN-86 regression: internal whitespace runs must collapse."
    )


def test_endpoint_uses_canonicalizer_for_fingerprint():
    """Source-level: api_wizard_complete must call the
    canonicalizer before hashing. Otherwise the helper exists
    but the cache stays brittle."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    assert "_canonicalize_complete_payload" in src, (
        "BRAIN-86 regression: api_wizard_complete must call "
        "`_canonicalize_complete_payload` before computing the "
        "SHA256 fingerprint. Without that call, the helper is "
        "decoration."
    )
