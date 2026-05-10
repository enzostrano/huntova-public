"""BRAIN-202: app.record_domain_fail invariant audit.

`record_domain_fail` increments a per-domain failure count used by
`is_blocked` to auto-block domains after 2 consecutive failures.
Bug-#69 fix routed through `_hostish_netloc` so schemeless URLs
record correctly (matching the key `is_blocked` reads).

Pinned invariants:

1. Empty / None URL is a no-op.
2. Schemeless URL extracts domain via `_hostish_netloc` (bug-#69).
3. Multiple calls accumulate the count.
4. Different URLs to the same domain converge on the same key.
5. Different domains have separate counts.
6. `is_blocked` returns True after 2+ failures (the documented threshold).
"""
from __future__ import annotations


def _reset_domain_fails():
    import app
    if hasattr(app, "_domain_fails"):
        app._domain_fails.clear()


def _ensure_no_ctx(monkeypatch):
    """Force the no-context branch (CLI-direct mode) so fails go to
    the global `_domain_fails` dict instead of a per-user ctx."""
    import app
    monkeypatch.setattr(app, "_ctx", lambda: None)


def test_empty_url_no_op(monkeypatch):
    from app import record_domain_fail
    _ensure_no_ctx(monkeypatch)
    _reset_domain_fails()
    record_domain_fail("")
    record_domain_fail(None)
    import app
    assert app._domain_fails == {}


def test_schemeless_url_recorded(monkeypatch):
    """Bug-#69: `competitor.com/jobs` (schemeless) extracts domain."""
    from app import record_domain_fail
    _ensure_no_ctx(monkeypatch)
    _reset_domain_fails()
    record_domain_fail("competitor.com/jobs")
    import app
    assert app._domain_fails.get("competitor.com") == 1


def test_full_url_recorded(monkeypatch):
    from app import record_domain_fail
    _ensure_no_ctx(monkeypatch)
    _reset_domain_fails()
    record_domain_fail("https://example.com/page")
    import app
    assert app._domain_fails.get("example.com") == 1


def test_accumulates_count(monkeypatch):
    from app import record_domain_fail
    _ensure_no_ctx(monkeypatch)
    _reset_domain_fails()
    record_domain_fail("https://example.com/x")
    record_domain_fail("https://example.com/y")
    record_domain_fail("https://example.com/z")
    import app
    assert app._domain_fails.get("example.com") == 3


def test_schemeless_and_schemed_converge_on_same_key(monkeypatch):
    """Bug-#69 closure: `competitor.com` and `https://competitor.com`
    record under the same key so is_blocked agrees."""
    from app import record_domain_fail
    _ensure_no_ctx(monkeypatch)
    _reset_domain_fails()
    record_domain_fail("competitor.com/x")
    record_domain_fail("https://competitor.com/y")
    import app
    assert app._domain_fails.get("competitor.com") == 2


def test_different_domains_separate_counts(monkeypatch):
    from app import record_domain_fail
    _ensure_no_ctx(monkeypatch)
    _reset_domain_fails()
    record_domain_fail("https://a.com/")
    record_domain_fail("https://b.com/")
    record_domain_fail("https://b.com/")
    import app
    assert app._domain_fails.get("a.com") == 1
    assert app._domain_fails.get("b.com") == 2


def test_www_prefix_normalised(monkeypatch):
    """`www.example.com` and `example.com` converge."""
    from app import record_domain_fail
    _ensure_no_ctx(monkeypatch)
    _reset_domain_fails()
    record_domain_fail("https://www.example.com/")
    record_domain_fail("https://example.com/")
    import app
    # Both reduce to "example.com" via _hostish_netloc.
    assert app._domain_fails.get("example.com") == 2


def test_is_blocked_after_2_fails(monkeypatch):
    """The auto-block threshold: 2+ failures → is_blocked True."""
    from app import record_domain_fail, is_blocked
    _ensure_no_ctx(monkeypatch)
    _reset_domain_fails()
    record_domain_fail("https://flaky.com/x")
    record_domain_fail("https://flaky.com/y")
    # 2 fails → blocked.
    assert is_blocked("https://flaky.com/z") is True


def test_is_blocked_after_1_fail_still_allowed(monkeypatch):
    """1 failure isn't enough — only after 2+."""
    from app import record_domain_fail, is_blocked
    _ensure_no_ctx(monkeypatch)
    _reset_domain_fails()
    record_domain_fail("https://once.com/x")
    # 1 fail → still allowed.
    assert is_blocked("https://once.com/y") is False
