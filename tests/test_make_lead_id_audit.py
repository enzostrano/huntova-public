"""BRAIN-201: app.make_lead_id invariant audit.

`make_lead_id` is the public lead identifier — a 12-char SHA256
hex prefix of `make_fingerprint(lead)`. Stable across runs;
identical leads always get the same id.

Pinned invariants:

1. Returns 12-char hex string.
2. Same input → same id (deterministic).
3. Different input → different id (probabilistically).
4. Empty lead doesn't crash.
"""
from __future__ import annotations


def test_make_lead_id_returns_12_hex_chars():
    from app import make_lead_id
    lid = make_lead_id({"org_website": "https://acme.com",
                         "country": "US"})
    assert isinstance(lid, str)
    assert len(lid) == 12
    int(lid, 16)  # must be valid hex


def test_make_lead_id_deterministic():
    from app import make_lead_id
    lead = {"org_website": "https://acme.com", "country": "US"}
    a = make_lead_id(lead)
    b = make_lead_id(dict(lead))  # different dict object, same data
    assert a == b


def test_make_lead_id_different_for_different_input():
    from app import make_lead_id
    a = make_lead_id({"org_website": "https://acme.com", "country": "US"})
    b = make_lead_id({"org_website": "https://other.com", "country": "US"})
    assert a != b


def test_make_lead_id_empty_lead_doesnt_crash():
    from app import make_lead_id
    out = make_lead_id({})
    assert isinstance(out, str)
    assert len(out) == 12


def test_make_lead_id_lowercase_hex():
    """SHA256 hexdigest is lowercase; pin so any future case-change
    is intentional."""
    from app import make_lead_id
    lid = make_lead_id({"org_website": "https://acme.com", "country": "US"})
    assert lid == lid.lower()
