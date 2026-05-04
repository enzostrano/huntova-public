"""Regression tests for the search abstraction + retry policy
(a811 — search abstraction audit).

These tests guard the SearXNG retry budget, the per-hunt circuit
breaker, and the failure-mode classification in `_searxng_query_once`.
They mock `_search_session.get` so no network is hit — every test
runs in <1s.

Bugs guarded against:

1. HTTPError (502/503) was silently swallowed by the bare
   `except Exception` branch — no retries, no SSE warning. A flaky
   SearXNG burned the whole hunt's quota by hammering DDG instead.
2. The breaker DID NOT exist — every query independently retried
   even when SearXNG had been down for 30 consecutive queries in a
   row. Now after SEARXNG_MAX_CONSEC_FAILURES failures the breaker
   trips and skips the SearXNG round-trip entirely.
3. 4xx (non-429) used to retry uselessly. Now bails fast.
4. 429 used to bail like a 4xx. Now treated as retryable.
"""
from __future__ import annotations

import sys
import importlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@pytest.fixture
def app_mod(monkeypatch, tmp_path):
    """Import app.py with side-effects neutered. We do NOT actually
    run the agent — we only need the search() function + breaker."""
    monkeypatch.setenv("APP_MODE", "local")
    monkeypatch.setenv("HUNTOVA_DB_PATH", str(tmp_path / "h.sqlite"))
    monkeypatch.setenv("HV_GEMINI_KEY", "test-key")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    if "app" in sys.modules:
        del sys.modules["app"]
    app = importlib.import_module("app")
    # Reset breaker so each test starts clean.
    app.reset_search_breaker()
    # Silence emit_log so the captured stdout stays readable.
    monkeypatch.setattr(app, "emit_log", lambda *a, **k: None)
    # Speed up tests — don't actually sleep on backoff.
    monkeypatch.setattr(app.time, "sleep", lambda *_a, **_k: None)
    # Stub _check_stop so it never returns True mid-test.
    monkeypatch.setattr(app, "_check_stop", lambda: False)
    # Stub the DDG fallback to a sentinel so we can detect when the
    # code falls through to it.
    _sentinel = [app.SearchResult(url="https://ddg.example/r1", title="ddg", snippet="")]
    monkeypatch.setattr(app, "_ddg_fallback_search", lambda q, n: list(_sentinel))
    app._test_ddg_sentinel = _sentinel
    return app


def _mock_response(status_code, json_body=None, raise_on_get=None):
    """Build a fake `requests.Response`-ish object for _search_session.get."""
    if raise_on_get is not None:
        raise raise_on_get
    r = MagicMock()
    r.status_code = status_code
    if json_body is None:
        r.json.side_effect = ValueError("no json")
    else:
        r.json.return_value = json_body
    return r


# ─────────────────────────── happy path ───────────────────────────


def test_searxng_success_returns_results(app_mod):
    """Normal SearXNG 200 with results — no fallback, no retries."""
    body = {"results": [
        {"url": "https://a.example", "title": "A", "content": "snippet a"},
        {"url": "https://b.example", "title": "B", "content": "snippet b"},
    ]}
    with patch.object(app_mod._search_session, "get",
                      return_value=_mock_response(200, body)) as mock_get:
        out = app_mod.search("hello")
    assert len(out) == 2
    assert out[0].url == "https://a.example"
    assert mock_get.call_count == 1  # no retries needed
    assert app_mod._searxng_breaker.consec_failures == 0
    assert app_mod._searxng_breaker.is_tripped() is False


def test_searxng_zero_results_falls_through_to_ddg(app_mod):
    """Valid 200 but zero hits — DDG runs as a peer engine without
    counting as a SearXNG failure."""
    body = {"results": []}
    with patch.object(app_mod._search_session, "get",
                      return_value=_mock_response(200, body)):
        out = app_mod.search("zero-hit query")
    # Returned the DDG sentinel — confirms fallback fired.
    assert len(out) == 1 and out[0].url == "https://ddg.example/r1"
    # But the breaker is NOT charged with a failure.
    assert app_mod._searxng_breaker.consec_failures == 0


# ────────────────────── retryable failures ────────────────────────


def test_searxng_502_retries_then_falls_back(app_mod):
    """502 is retryable. Should attempt SEARXNG_MAX_RETRIES_PER_QUERY+1
    times then DDG."""
    bad = _mock_response(502)
    with patch.object(app_mod._search_session, "get",
                      return_value=bad) as mock_get:
        out = app_mod.search("flaky")
    # 1 initial + SEARXNG_MAX_RETRIES_PER_QUERY retries
    expected = app_mod.SEARXNG_MAX_RETRIES_PER_QUERY + 1
    assert mock_get.call_count == expected
    # Returned DDG sentinel
    assert len(out) == 1 and out[0].url == "https://ddg.example/r1"
    # Charged with one failure
    assert app_mod._searxng_breaker.consec_failures == 1


def test_searxng_503_retries_then_falls_back(app_mod):
    """503 (the canonical "service unavailable") is retryable."""
    with patch.object(app_mod._search_session, "get",
                      return_value=_mock_response(503)) as mock_get:
        app_mod.search("503 test")
    assert mock_get.call_count == app_mod.SEARXNG_MAX_RETRIES_PER_QUERY + 1


def test_searxng_429_is_retryable(app_mod):
    """429 (rate-limited) is retryable — used to bail like a 4xx."""
    with patch.object(app_mod._search_session, "get",
                      return_value=_mock_response(429)) as mock_get:
        app_mod.search("rate-limited")
    assert mock_get.call_count == app_mod.SEARXNG_MAX_RETRIES_PER_QUERY + 1
    assert app_mod._searxng_breaker.consec_failures == 1


def test_searxng_timeout_retries(app_mod):
    """ConnectTimeout/ReadTimeout retries within budget."""
    import requests
    with patch.object(app_mod._search_session, "get",
                      side_effect=requests.exceptions.Timeout()) as mock_get:
        app_mod.search("timeout")
    assert mock_get.call_count == app_mod.SEARXNG_MAX_RETRIES_PER_QUERY + 1
    assert app_mod._searxng_breaker.consec_failures == 1


def test_searxng_connection_error_retries(app_mod):
    """ConnectionError retries within budget."""
    import requests
    with patch.object(app_mod._search_session, "get",
                      side_effect=requests.exceptions.ConnectionError()) as mock_get:
        app_mod.search("conn-refused")
    assert mock_get.call_count == app_mod.SEARXNG_MAX_RETRIES_PER_QUERY + 1
    assert app_mod._searxng_breaker.consec_failures == 1


def test_searxng_json_decode_failure_retries(app_mod):
    """200 with non-JSON body retries (instance may have JSON API
    disabled, but a single bad cache hit shouldn't kill the hunt)."""
    bad = MagicMock()
    bad.status_code = 200
    bad.json.side_effect = ValueError("not json")
    with patch.object(app_mod._search_session, "get",
                      return_value=bad) as mock_get:
        app_mod.search("decode test")
    assert mock_get.call_count == app_mod.SEARXNG_MAX_RETRIES_PER_QUERY + 1


# ─────────────────────── non-retryable 4xx ────────────────────────


def test_searxng_404_does_NOT_retry(app_mod):
    """404 / generic 4xx (other than 429) is a permanent failure for
    THIS query — bail without retries."""
    with patch.object(app_mod._search_session, "get",
                      return_value=_mock_response(404)) as mock_get:
        out = app_mod.search("bad query")
    # ONE attempt only.
    assert mock_get.call_count == 1
    assert len(out) == 1 and out[0].url == "https://ddg.example/r1"
    # 4xx must NOT trip the breaker — could be a query problem, not
    # an outage.
    assert app_mod._searxng_breaker.consec_failures == 0
    assert app_mod._searxng_breaker.is_tripped() is False


def test_searxng_400_does_NOT_retry(app_mod):
    with patch.object(app_mod._search_session, "get",
                      return_value=_mock_response(400)) as mock_get:
        app_mod.search("malformed")
    assert mock_get.call_count == 1
    assert app_mod._searxng_breaker.consec_failures == 0


# ─────────────────────── circuit breaker ──────────────────────────


def test_breaker_trips_after_consecutive_failures(app_mod):
    """N consecutive failures → breaker trips → subsequent calls skip
    SearXNG entirely."""
    with patch.object(app_mod._search_session, "get",
                      return_value=_mock_response(502)) as mock_get:
        # Drive enough failures to trip the breaker.
        for _ in range(app_mod.SEARXNG_MAX_CONSEC_FAILURES):
            app_mod.search("flaky")
        # Breaker tripped now.
        assert app_mod._searxng_breaker.is_tripped() is True
        prior_calls = mock_get.call_count
        # Fire MORE queries — they should NOT touch SearXNG.
        for _ in range(5):
            out = app_mod.search("post-trip")
            assert out[0].url == "https://ddg.example/r1"
        assert mock_get.call_count == prior_calls, (
            "Tripped breaker must skip SearXNG entirely — saw extra "
            f"{mock_get.call_count - prior_calls} requests"
        )


def test_breaker_resets_on_success(app_mod):
    """A success should clear consecutive-failure count so a one-off
    flake doesn't trip the breaker."""
    # search("a") consumes (1 + retries) bad attempts → consec_failures=1
    # search("b") consumes 1 good attempt        → consec_failures=0
    # search("c") consumes (1 + retries) bad attempts → consec_failures=1
    bad = _mock_response(502)
    good = _mock_response(200, {"results": [
        {"url": "https://ok.example", "title": "ok", "content": ""}
    ]})
    retries = app_mod.SEARXNG_MAX_RETRIES_PER_QUERY
    n_attempts_per_fail = retries + 1
    # Build a sequence that returns `bad` for the first failure window,
    # `good` for the next call, then `bad` again for the second failure.
    seq = ([bad] * n_attempts_per_fail
           + [good]
           + [bad] * n_attempts_per_fail)
    with patch.object(app_mod._search_session, "get",
                      side_effect=lambda *a, **k: seq.pop(0)) as _mg:
        app_mod.search("a")
        assert app_mod._searxng_breaker.consec_failures == 1
        app_mod.search("b")
        assert app_mod._searxng_breaker.consec_failures == 0
        app_mod.search("c")
        assert app_mod._searxng_breaker.consec_failures == 1
    assert app_mod._searxng_breaker.is_tripped() is False


def test_breaker_warning_emitted_only_once(app_mod):
    """The user-facing 'search backend degraded' SSE warning must
    fire exactly once per hunt — not on every fallback query."""
    warnings = []
    app_mod.emit_log = lambda msg, level="info": warnings.append((level, msg))

    with patch.object(app_mod._search_session, "get",
                      return_value=_mock_response(502)):
        for _ in range(app_mod.SEARXNG_MAX_CONSEC_FAILURES + 5):
            app_mod.search("warn-test")

    degraded = [m for (_lvl, m) in warnings if "degraded" in m.lower()]
    assert len(degraded) == 1, (
        f"Expected exactly one 'degraded' warning per hunt, got "
        f"{len(degraded)}: {degraded}"
    )


def test_reset_search_breaker_clears_state(app_mod):
    """`reset_search_breaker()` must clear failure count + warned
    flag so a previous hunt's failures don't leak into a new one."""
    with patch.object(app_mod._search_session, "get",
                      return_value=_mock_response(502)):
        for _ in range(app_mod.SEARXNG_MAX_CONSEC_FAILURES):
            app_mod.search("trip")
    assert app_mod._searxng_breaker.is_tripped() is True

    app_mod.reset_search_breaker()
    assert app_mod._searxng_breaker.is_tripped() is False
    assert app_mod._searxng_breaker.consec_failures == 0
    assert app_mod._searxng_breaker.warned is False


# ────────── _searxng_query_once classifier (lower-level) ───────────


def test_query_once_classifies_5xx_as_http_5xx(app_mod):
    with patch.object(app_mod._search_session, "get",
                      return_value=_mock_response(502)):
        results, err = app_mod._searxng_query_once("q", 5, "en", "", "general", 6)
    assert results == []
    assert err == "http_5xx"


def test_query_once_classifies_404_as_http_4xx(app_mod):
    with patch.object(app_mod._search_session, "get",
                      return_value=_mock_response(404)):
        results, err = app_mod._searxng_query_once("q", 5, "en", "", "general", 6)
    assert err == "http_4xx"


def test_query_once_classifies_429_as_5xx(app_mod):
    """429 should be treated as retryable, not 4xx-bail."""
    with patch.object(app_mod._search_session, "get",
                      return_value=_mock_response(429)):
        results, err = app_mod._searxng_query_once("q", 5, "en", "", "general", 6)
    assert err == "http_5xx"


def test_query_once_classifies_timeout(app_mod):
    import requests
    with patch.object(app_mod._search_session, "get",
                      side_effect=requests.exceptions.Timeout()):
        results, err = app_mod._searxng_query_once("q", 5, "en", "", "general", 6)
    assert err == "timeout"


def test_query_once_classifies_connection_error(app_mod):
    import requests
    with patch.object(app_mod._search_session, "get",
                      side_effect=requests.exceptions.ConnectionError()):
        results, err = app_mod._searxng_query_once("q", 5, "en", "", "general", 6)
    assert err == "conn"


def test_query_once_decode_failure(app_mod):
    bad = MagicMock()
    bad.status_code = 200
    bad.json.side_effect = ValueError("not json")
    with patch.object(app_mod._search_session, "get", return_value=bad):
        results, err = app_mod._searxng_query_once("q", 5, "en", "", "general", 6)
    assert err == "decode"


def test_query_once_caps_results_at_max(app_mod):
    """max_results bound is honoured even when SearXNG returns more."""
    body = {"results": [
        {"url": f"https://e{i}.example", "title": f"t{i}", "content": ""}
        for i in range(20)
    ]}
    with patch.object(app_mod._search_session, "get",
                      return_value=_mock_response(200, body)):
        results, err = app_mod._searxng_query_once("q", 5, "en", "", "general", 6)
    assert err is None
    assert len(results) == 5


def test_query_once_drops_non_http_urls(app_mod):
    body = {"results": [
        {"url": "javascript:alert(1)", "title": "bad", "content": ""},
        {"url": "ftp://nope.example", "title": "bad2", "content": ""},
        {"url": "https://good.example", "title": "good", "content": ""},
    ]}
    with patch.object(app_mod._search_session, "get",
                      return_value=_mock_response(200, body)):
        results, err = app_mod._searxng_query_once("q", 5, "en", "", "general", 6)
    assert err is None
    assert len(results) == 1
    assert results[0].url == "https://good.example"
