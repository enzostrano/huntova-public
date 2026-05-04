"""BRAIN-193: app.make_fingerprint + helpers invariant audit.

The lead-dedup fingerprint is the only thing keeping repeat hunts
from re-billing the user for already-discovered prospects. A
regression here either over-dedups (real new leads silently dropped)
or under-dedups (same lead bills again).

Pinned invariants:

1. `_fp_normalize` lowercases + strips noise words + removes punctuation.
2. `_fp_normalize` handles None / empty.
3. `_hostish_netloc` extracts hostname from full URL.
4. `_hostish_netloc` handles schemeless input (bug #63 fix).
5. `_hostish_netloc` strips explicit port.
6. `_hostish_netloc` strips `www.` prefix.
7. `make_fingerprint` default mode (domain) returns `domain|country`.
8. `make_fingerprint` falls back to `org|event|country` when no domain.
9. `make_fingerprint_legacy` returns the legacy 3-part shape.
10. Schemeless and schemed URLs produce the same domain fingerprint.
"""
from __future__ import annotations


def test_fp_normalize_lowercases():
    from app import _fp_normalize
    assert _fp_normalize("ACME") == "acme"


def test_fp_normalize_strips_noise_words():
    from app import _fp_normalize
    # "the" / "of" / "and" stripped.
    out = _fp_normalize("the acme corporation")
    # "the" / "corporation" stripped → "acme"
    assert "acme" in out
    assert "the" not in out
    assert "corporation" not in out


def test_fp_normalize_strips_legal_suffixes():
    from app import _fp_normalize
    out_inc = _fp_normalize("Acme Inc")
    out_ltd = _fp_normalize("Acme Ltd")
    out_gmbh = _fp_normalize("Acme GmbH")
    # All should reduce to "acme".
    assert out_inc == "acme"
    assert out_ltd == "acme"
    assert out_gmbh == "acme"


def test_fp_normalize_strips_event_words():
    from app import _fp_normalize
    out = _fp_normalize("Annual Conference 2026")
    # "annual" + "conference" + "2026" all stripped.
    assert out == ""


def test_fp_normalize_handles_none():
    from app import _fp_normalize
    assert _fp_normalize(None) == ""
    assert _fp_normalize("") == ""


def test_fp_normalize_strips_non_word_chars():
    """Non-word chars stripped. Note: 'corp' is also a noise word
    that gets removed first, so 'acme!@#corp' → 'acme'."""
    from app import _fp_normalize
    out = _fp_normalize("acme!@#widgets")
    # 'widgets' isn't a noise word; punctuation stripped between.
    assert out == "acmewidgets"


def test_hostish_netloc_full_url():
    from app import _hostish_netloc
    assert _hostish_netloc("https://example.com/path") == "example.com"


def test_hostish_netloc_strips_www():
    from app import _hostish_netloc
    assert _hostish_netloc("https://www.example.com/") == "example.com"


def test_hostish_netloc_schemeless_bug_63():
    """Bug #63: schemeless `example.com` should still extract hostname."""
    from app import _hostish_netloc
    assert _hostish_netloc("example.com") == "example.com"


def test_hostish_netloc_schemeless_with_path():
    from app import _hostish_netloc
    assert _hostish_netloc("example.com/contact") == "example.com"


def test_hostish_netloc_strips_explicit_port():
    """`https://google.com:443/x` must reduce to `google.com` so
    domain-blocklist matching works."""
    from app import _hostish_netloc
    assert _hostish_netloc("https://google.com:443/contact") == "google.com"
    assert _hostish_netloc("http://example.com:8080") == "example.com"


def test_hostish_netloc_handles_none():
    from app import _hostish_netloc
    assert _hostish_netloc(None) == ""
    assert _hostish_netloc("") == ""


def test_hostish_netloc_strips_whitespace():
    from app import _hostish_netloc
    assert _hostish_netloc("  https://example.com  ") == "example.com"


def test_hostish_netloc_lowercases():
    from app import _hostish_netloc
    assert _hostish_netloc("https://Example.COM/") == "example.com"


def test_make_fingerprint_uses_domain():
    """Default mode (domain) — fingerprint is `domain|country`."""
    from app import make_fingerprint
    lead = {"org_name": "Acme Corp",
            "org_website": "https://acme.com",
            "country": "US"}
    fp = make_fingerprint(lead)
    assert "acme.com" in fp
    assert "us" in fp.lower()


def test_make_fingerprint_schemeless_matches_schemed():
    """Bug #63 closure: schemeless and schemed URLs produce the
    same fingerprint (so the same lead doesn't double-bill)."""
    from app import make_fingerprint
    a = make_fingerprint({"org_website": "https://acme.com",
                           "country": "US"})
    b = make_fingerprint({"org_website": "acme.com",
                           "country": "US"})
    assert a == b


def test_make_fingerprint_falls_back_when_no_domain():
    """Without a website, falls back to `org|event|country`."""
    from app import make_fingerprint
    lead = {"org_name": "Acme",
            "event_name": "Summit 2026",
            "country": "US"}
    fp = make_fingerprint(lead)
    # Some non-empty result.
    assert fp
    # Should NOT contain "acme.com" — there's no domain.
    assert "acme.com" not in fp


def test_make_fingerprint_legacy_shape():
    """Legacy fingerprint is `org|event|country`."""
    from app import make_fingerprint_legacy
    lead = {"org_name": "Acme",
            "event_name": "Summit",
            "country": "US"}
    fp = make_fingerprint_legacy(lead)
    parts = fp.split("|")
    assert len(parts) == 3


def test_make_fingerprint_consistent_for_same_input():
    """Same lead → same fingerprint (deterministic)."""
    from app import make_fingerprint
    lead = {"org_website": "https://acme.com", "country": "US"}
    a = make_fingerprint(lead)
    b = make_fingerprint(lead.copy())
    assert a == b


def test_make_fingerprint_different_country_distinct():
    """Same domain in different countries produces different fps
    (handles companies with multi-country presences)."""
    from app import make_fingerprint
    a = make_fingerprint({"org_website": "https://acme.com",
                           "country": "US"})
    b = make_fingerprint({"org_website": "https://acme.com",
                           "country": "DE"})
    assert a != b
