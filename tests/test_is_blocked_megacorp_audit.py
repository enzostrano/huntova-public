"""BRAIN-203: app.is_blocked mega-corp domain check audit.

`is_blocked` rejects URLs whose domain matches MEGA_CORP_DOMAINS
(Fortune 500 / never-B2B-prospects list). Bug-#69 fix uses
`_hostish_netloc` so schemeless URLs don't bypass the gate.

Pinned invariants:

1. Empty / None URL returns False.
2. Mega-corp domain (e.g. google.com) returns True.
3. Subdomain of mega-corp (e.g. translate.google.com) returns True.
4. Schemeless mega-corp URL (`google.com/jobs`) returns True (bug-#69).
5. Non-mega-corp domains return False (when no fail count).
6. Substring-only matches don't trigger (e.g. `notgoogle.com`).
7. `www.google.com` matches `google.com` in MEGA_CORP_DOMAINS.
"""
from __future__ import annotations


def _ensure_no_ctx(monkeypatch):
    import app
    monkeypatch.setattr(app, "_ctx", lambda: None)
    if hasattr(app, "_domain_fails"):
        app._domain_fails.clear()


def test_is_blocked_empty_url(monkeypatch):
    _ensure_no_ctx(monkeypatch)
    from app import is_blocked
    assert is_blocked("") is False
    assert is_blocked(None) is False


def test_is_blocked_google_com(monkeypatch):
    """google.com is in MEGA_CORP_DOMAINS — must be blocked."""
    _ensure_no_ctx(monkeypatch)
    from app import is_blocked
    assert is_blocked("https://google.com/") is True
    assert is_blocked("https://www.google.com/") is True


def test_is_blocked_subdomain_of_mega_corp(monkeypatch):
    """Subdomain match: translate.google.com → blocked."""
    _ensure_no_ctx(monkeypatch)
    from app import is_blocked
    assert is_blocked("https://translate.google.com/") is True
    assert is_blocked("https://docs.google.com/") is True


def test_is_blocked_schemeless_mega_corp(monkeypatch):
    """Bug-#69: `google.com/page` (schemeless) still matches."""
    _ensure_no_ctx(monkeypatch)
    from app import is_blocked
    assert is_blocked("google.com/jobs") is True
    assert is_blocked("microsoft.com") is True


def test_is_blocked_non_mega_corp(monkeypatch):
    """A regular small business domain is NOT blocked."""
    _ensure_no_ctx(monkeypatch)
    from app import is_blocked
    assert is_blocked("https://small-business-co.com/") is False
    assert is_blocked("https://acme-widgets-llc.com/") is False


def test_is_blocked_substring_does_not_match(monkeypatch):
    """`notgoogle.com` doesn't match `google.com` (suffix-only check)."""
    _ensure_no_ctx(monkeypatch)
    from app import is_blocked
    assert is_blocked("https://notgoogle.com/") is False
    assert is_blocked("https://googleish.com/") is False


def test_is_blocked_partial_match_in_path(monkeypatch):
    """A URL with `google.com` in the path but a different host
    isn't blocked."""
    _ensure_no_ctx(monkeypatch)
    from app import is_blocked
    assert is_blocked("https://example.com/google.com/page") is False


def test_is_blocked_news_outlets(monkeypatch):
    """News domains in MEGA_CORP_DOMAINS — bbc.com, cnn.com, nytimes.com."""
    _ensure_no_ctx(monkeypatch)
    from app import is_blocked
    assert is_blocked("https://bbc.com/news") is True
    assert is_blocked("https://cnn.com/") is True


def test_is_blocked_retail(monkeypatch):
    """Retail domains in MEGA_CORP_DOMAINS — never B2B prospects."""
    _ensure_no_ctx(monkeypatch)
    from app import is_blocked
    assert is_blocked("https://walmart.com/") is True
    assert is_blocked("https://amazon.com/") is True


def test_is_blocked_event_platforms(monkeypatch):
    """Event-platform domains in MEGA_CORP_DOMAINS (eventbrite, hopin
    etc.) — never the actual organisation, so skip."""
    _ensure_no_ctx(monkeypatch)
    from app import is_blocked
    assert is_blocked("https://eventbrite.com/e/some-event") is True
    assert is_blocked("https://zoom.us/j/123") is True


def test_is_blocked_after_2_fails_overrides(monkeypatch):
    """Even if a domain isn't in MEGA_CORP_DOMAINS, 2+ fails → blocked."""
    from app import record_domain_fail, is_blocked
    _ensure_no_ctx(monkeypatch)
    record_domain_fail("https://flaky.com/x")
    record_domain_fail("https://flaky.com/y")
    assert is_blocked("https://flaky.com/z") is True
