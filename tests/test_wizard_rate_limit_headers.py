"""Regression tests for BRAIN-112 (a481): wizard 429
responses must carry machine-readable backoff hints.

Failure mode (Per Huntova engineering review on
rate-limited APIs and IETF draft-ietf-httpapi-ratelimit-headers):

The wizard 429 paths (per-minute burst buckets via
`_check_ai_rate` AND per-day quotas via
`_check_paid_endpoint_quota_async` /
`_check_scan_daily_quota_async`) currently emit a bare
`status_code=429` with `{"error": "..."}` JSON and no
HTTP headers carrying backoff hints. Clients see "429"
with no idea WHEN to retry — so they either:

1. Hammer the endpoint immediately and re-trip the same
   429, burning user patience and producing log noise.
2. Back off arbitrarily long (e.g. 5 minutes) when the
   actual reset is 30 seconds — wasted user time.
3. Implement their own ad-hoc retry timing that drifts
   from the server's actual policy as we tune buckets.

Standard contract (RFC 6585 + IETF draft RateLimit-*):
- `Retry-After: <seconds>` on every 429.
- `RateLimit-Limit: <max-calls>` on every response (so
  clients can throttle proactively).
- `RateLimit-Remaining: <calls-left>` on every response.
- `RateLimit-Reset: <seconds-until-window-resets>` on
  every response.

For DAILY quotas, the reset horizon is "next 00:00 UTC"
not "X seconds from now", but Retry-After can still
carry the seconds-until-midnight value.

Invariants:
- Module-scope helper `_rate_limit_429(user_id, bucket,
  message, error_kind=None)` exists and returns a
  JSONResponse with Retry-After + RateLimit-* headers.
- Module-scope helper `_daily_quota_429(daily_max,
  message, error_kind)` exists and returns a
  JSONResponse with Retry-After (seconds until UTC
  midnight) + RateLimit-Limit + RateLimit-Remaining=0 +
  RateLimit-Reset.
- Wizard 429 emission sites (scan, complete,
  generate-phase5, assist, save-progress, reset) use
  the helpers — never bare `status_code=429`.
"""
from __future__ import annotations
import inspect


def test_rate_limit_429_helper_exists():
    """Module-scope helper for burst-bucket 429s."""
    import server as _s
    fn = getattr(_s, "_rate_limit_429", None)
    assert fn is not None and callable(fn), (
        "BRAIN-112 regression: server must expose "
        "`_rate_limit_429(user_id, bucket, message, "
        "error_kind=None)` so every burst-bucket 429 site "
        "uses the same Retry-After + RateLimit-* contract."
    )


def test_daily_quota_429_helper_exists():
    """Module-scope helper for daily-quota 429s."""
    import server as _s
    fn = getattr(_s, "_daily_quota_429", None)
    assert fn is not None and callable(fn), (
        "BRAIN-112 regression: server must expose "
        "`_daily_quota_429(daily_max, message, "
        "error_kind)` so every daily-quota 429 site "
        "carries Retry-After (seconds until UTC midnight) "
        "+ RateLimit-* headers."
    )


def test_rate_limit_429_emits_retry_after():
    """Behavioral: the burst-bucket helper produces a
    response with a numeric Retry-After header."""
    import server as _s
    # bucket name doesn't matter for the test; we just
    # call the function and inspect the headers.
    resp = _s._rate_limit_429(
        user_id=42, bucket="wizard_scan",
        message="Too many scan requests."
    )
    headers = {k.lower(): v for k, v in resp.headers.items()}
    assert "retry-after" in headers, (
        "BRAIN-112 regression: burst-bucket 429 must "
        "carry Retry-After."
    )
    # Retry-After must parse as a positive int (seconds).
    val = int(headers["retry-after"])
    assert val > 0
    assert val <= 120, (
        "BRAIN-112 regression: burst-bucket Retry-After "
        "should be <= the bucket window (60s for the "
        "wizard buckets, with a small safety margin); a "
        "larger value would tell clients to wait longer "
        "than necessary."
    )


def test_rate_limit_429_emits_ratelimit_headers():
    """Behavioral: burst-bucket 429 must carry
    RateLimit-Limit, RateLimit-Remaining, RateLimit-Reset
    so clients can throttle proactively."""
    import server as _s
    resp = _s._rate_limit_429(
        user_id=42, bucket="wizard_scan", message="..."
    )
    headers = {k.lower(): v for k, v in resp.headers.items()}
    for h in ("ratelimit-limit", "ratelimit-remaining", "ratelimit-reset"):
        assert h in headers, (
            f"BRAIN-112 regression: burst-bucket 429 missing "
            f"`{h}` header — clients can't throttle "
            f"proactively without the full triple."
        )
    # On a 429, remaining is 0 (we just hit the wall).
    assert int(headers["ratelimit-remaining"]) == 0


def test_rate_limit_429_status_is_429():
    """Sanity: helper returns a 429 status."""
    import server as _s
    resp = _s._rate_limit_429(user_id=42, bucket="wizard_scan", message="...")
    assert resp.status_code == 429


def test_daily_quota_429_emits_retry_after_until_midnight():
    """Behavioral: daily-quota helper Retry-After is
    seconds until next UTC midnight (or capped to 24h)."""
    import server as _s
    resp = _s._daily_quota_429(
        daily_max=50,
        message="Daily quota exceeded.",
        error_kind="scan_daily_quota_exceeded",
    )
    headers = {k.lower(): v for k, v in resp.headers.items()}
    assert "retry-after" in headers
    val = int(headers["retry-after"])
    # Strictly less than 25 hours, strictly greater than 0
    # (something to wait for, but not absurdly long).
    assert 0 < val <= 25 * 3600


def test_daily_quota_429_status_is_429():
    """Sanity."""
    import server as _s
    resp = _s._daily_quota_429(
        daily_max=50, message="x", error_kind="x"
    )
    assert resp.status_code == 429


def test_daily_quota_429_emits_ratelimit_limit_and_remaining_zero():
    """Daily-quota 429 must announce the limit + zero
    remaining so clients know they've hit the wall."""
    import server as _s
    resp = _s._daily_quota_429(
        daily_max=50, message="x", error_kind="x"
    )
    headers = {k.lower(): v for k, v in resp.headers.items()}
    assert int(headers["ratelimit-limit"]) == 50
    assert int(headers["ratelimit-remaining"]) == 0


def test_wizard_scan_uses_rate_limit_helper():
    """Source-level: /api/wizard/scan must use
    `_rate_limit_429` for the burst-bucket 429."""
    from server import api_wizard_scan
    src = inspect.getsource(api_wizard_scan)
    assert "_rate_limit_429(" in src, (
        "BRAIN-112 regression: api_wizard_scan must use "
        "the shared `_rate_limit_429` helper instead of "
        "bare JSONResponse(..., status_code=429). Without "
        "the helper, scan 429s lack Retry-After + "
        "RateLimit-* headers."
    )


def test_wizard_assist_uses_helpers_for_both_429_paths():
    """Source-level: /api/wizard/assist has TWO 429 paths
    (burst bucket + daily quota). Both must use the
    matching helper."""
    from server import api_wizard_assist
    src = inspect.getsource(api_wizard_assist)
    assert "_rate_limit_429(" in src, (
        "BRAIN-112 regression: api_wizard_assist must use "
        "_rate_limit_429 for the burst-bucket 429."
    )
    assert "_daily_quota_429(" in src, (
        "BRAIN-112 regression: api_wizard_assist must use "
        "_daily_quota_429 for the daily-quota 429 (parity "
        "across rate-limited paths)."
    )


def test_wizard_complete_uses_rate_limit_helper():
    """Source-level: /api/wizard/complete must use the
    helper for its burst-bucket 429."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    assert "_rate_limit_429(" in src, (
        "BRAIN-112 regression: api_wizard_complete must "
        "use _rate_limit_429 for the burst-bucket 429."
    )


def test_wizard_phase5_uses_rate_limit_helper():
    """Source-level: /api/wizard/generate-phase5 must use
    the helper."""
    from server import api_wizard_generate_phase5
    src = inspect.getsource(api_wizard_generate_phase5)
    assert "_rate_limit_429(" in src, (
        "BRAIN-112 regression: api_wizard_generate_phase5 "
        "must use _rate_limit_429."
    )


def test_wizard_save_progress_uses_rate_limit_helper():
    """Source-level: save-progress must use the helper."""
    from server import api_wizard_save_progress
    src = inspect.getsource(api_wizard_save_progress)
    assert "_rate_limit_429(" in src, (
        "BRAIN-112 regression: api_wizard_save_progress "
        "must use _rate_limit_429."
    )


def test_wizard_reset_uses_rate_limit_helper():
    """Source-level: reset must use the helper."""
    from server import api_wizard_reset
    src = inspect.getsource(api_wizard_reset)
    assert "_rate_limit_429(" in src, (
        "BRAIN-112 regression: api_wizard_reset must use "
        "_rate_limit_429."
    )
