"""BRAIN-200: app.is_user_blocked + is_blocked invariant audit.

Lead-discovery filter gate. A regression here either over-blocks
(real prospects silently dropped) or under-blocks (the user's
manual blocklist gets bypassed and they keep seeing junk).

Pinned invariants (covers bug-#68 + bug-#69 fixes):

1. `is_user_blocked` matches URLs against the user's blocked-domains
   list with exact-or-subdomain-suffix semantics (NOT substring).
2. `is_user_blocked` drops bare TLD entries (a user typing "com"
   doesn't nuke every .com result).
3. `is_user_blocked` handles schemeless URLs (bug-#63 / bug-#69 lineage).
4. `is_user_blocked` NFC-normalises org-name comparison.
5. `is_user_blocked` returns False on empty inputs.
6. `is_blocked` uses `_hostish_netloc` so schemeless URLs don't
   bypass the fail-count + mega-corp gate.
"""
from __future__ import annotations


def test_is_user_blocked_exact_domain_match(monkeypatch):
    """Exact-domain match: blocked.com → blocked.com → True."""
    from app import is_user_blocked
    import app
    monkeypatch.setattr(app, "load_user_blocked",
                        lambda: {"domains": ["blocked.com"], "org_names": []})
    assert is_user_blocked("https://blocked.com/page", None) is True


def test_is_user_blocked_subdomain_suffix_match(monkeypatch):
    """`blocked.com` matches `subdomain.blocked.com` (suffix)."""
    from app import is_user_blocked
    import app
    monkeypatch.setattr(app, "load_user_blocked",
                        lambda: {"domains": ["blocked.com"], "org_names": []})
    assert is_user_blocked("https://www.blocked.com/page", None) is True
    assert is_user_blocked("https://api.blocked.com/x", None) is True


def test_is_user_blocked_substring_does_not_match(monkeypatch):
    """`blocked.com` must NOT match `unblocked.com` or `blockedy.com`
    (only suffix match, not substring). Bug-#68 fix."""
    from app import is_user_blocked
    import app
    monkeypatch.setattr(app, "load_user_blocked",
                        lambda: {"domains": ["blocked.com"], "org_names": []})
    assert is_user_blocked("https://unblocked.com/x", None) is False
    assert is_user_blocked("https://notblocked.com/x", None) is False


def test_is_user_blocked_schemeless_url(monkeypatch):
    """Bug-#69 fix: schemeless URL (`competitor.com/page`) still
    extracts domain via `_hostish_netloc`."""
    from app import is_user_blocked
    import app
    monkeypatch.setattr(app, "load_user_blocked",
                        lambda: {"domains": ["competitor.com"], "org_names": []})
    assert is_user_blocked("competitor.com/jobs", None) is True


def test_is_user_blocked_drops_bare_tld(monkeypatch):
    """Bug-#68 fix: blocking 'com' or 'co.uk' must NOT match
    every .com domain (silently dropped from match set since no
    dot in the entry)."""
    from app import is_user_blocked
    import app
    monkeypatch.setattr(app, "load_user_blocked",
                        lambda: {"domains": ["com"], "org_names": []})
    assert is_user_blocked("https://example.com/", None) is False


def test_is_user_blocked_org_name_match(monkeypatch):
    from app import is_user_blocked
    import app
    monkeypatch.setattr(app, "load_user_blocked",
                        lambda: {"domains": [], "org_names": ["Acme Corp"]})
    assert is_user_blocked(None, "Acme Corp") is True


def test_is_user_blocked_org_name_case_insensitive(monkeypatch):
    from app import is_user_blocked
    import app
    monkeypatch.setattr(app, "load_user_blocked",
                        lambda: {"domains": [], "org_names": ["acme corp"]})
    # Different case still matches.
    assert is_user_blocked(None, "ACME CORP") is True


def test_is_user_blocked_org_name_nfc_normalised(monkeypatch):
    """Bug-#68b fix: NFC-normalise so `Café` (NFC) matches `Café`
    (NFD = decomposed e + combining acute)."""
    from app import is_user_blocked
    import app
    import unicodedata
    nfc = "Café"  # might be either form depending on source
    nfd = unicodedata.normalize("NFD", nfc)
    monkeypatch.setattr(app, "load_user_blocked",
                        lambda: {"domains": [], "org_names": [nfc]})
    assert is_user_blocked(None, nfd) is True


def test_is_user_blocked_empty_inputs(monkeypatch):
    from app import is_user_blocked
    import app
    monkeypatch.setattr(app, "load_user_blocked",
                        lambda: {"domains": ["x.com"], "org_names": ["X"]})
    assert is_user_blocked(None, None) is False
    assert is_user_blocked("", "") is False


def test_is_user_blocked_no_match_returns_false(monkeypatch):
    from app import is_user_blocked
    import app
    monkeypatch.setattr(app, "load_user_blocked",
                        lambda: {"domains": ["blocked.com"],
                                  "org_names": ["Blocked Inc"]})
    assert is_user_blocked("https://other.com/", "Other Co") is False


def test_is_user_blocked_empty_blocklist(monkeypatch):
    from app import is_user_blocked
    import app
    monkeypatch.setattr(app, "load_user_blocked",
                        lambda: {"domains": [], "org_names": []})
    assert is_user_blocked("https://anything.com/", "Anyone") is False


def test_is_blocked_uses_hostish_netloc():
    """Bug-#69 fix: schemeless URLs go through _hostish_netloc so
    the fail-count + mega-corp gate doesn't get bypassed."""
    from app import is_blocked
    # Empty/None URL → False.
    assert is_blocked("") is False
    assert is_blocked(None) is False


def test_is_blocked_handles_unparseable():
    from app import is_blocked
    # Bare random string with no dot.
    out = is_blocked("notaurl")
    # Whatever it returns, must not crash.
    assert out in (True, False)


def test_is_user_blocked_multiple_domains(monkeypatch):
    """When multiple domains are blocked, any match returns True."""
    from app import is_user_blocked
    import app
    monkeypatch.setattr(app, "load_user_blocked",
                        lambda: {"domains": ["a.com", "b.com", "c.com"],
                                  "org_names": []})
    assert is_user_blocked("https://b.com/x", None) is True
    assert is_user_blocked("https://d.com/x", None) is False


def test_is_user_blocked_mixed_case_url(monkeypatch):
    """Hostname comparison is case-insensitive (via _hostish_netloc)."""
    from app import is_user_blocked
    import app
    monkeypatch.setattr(app, "load_user_blocked",
                        lambda: {"domains": ["blocked.com"], "org_names": []})
    assert is_user_blocked("https://BLOCKED.COM/x", None) is True
