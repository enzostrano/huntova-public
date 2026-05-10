"""Regression tests for BRAIN-113 (a482): wizard 200
responses on rate-limited routes must carry the same
RateLimit-Limit / RateLimit-Remaining / RateLimit-Reset
headers as the 429 path (BRAIN-112). Without them,
clients can only learn the budget by accidentally
exceeding it.

Failure mode (Per Huntova engineering review on
rate-limit headers + IETF draft-ietf-httpapi-ratelimit-
headers):

BRAIN-112 (a481) closed the bare-429 gap: every wizard
rate-limited 429 now carries machine-readable backoff
hints. But the headers fired ONLY on 429. A client that
made the call successfully had no way to see budget
depletion until it tripped the limiter — so retry logic
became reactive and wasteful, multi-tab and high-
latency clients stampeded the bucket because they
couldn't see the budget shrinking, and avoidable 429s
stacked up on hot paths like assist + save-progress.

Standard contract (IETF draft):
- `RateLimit-Limit` / `RateLimit-Remaining` /
  `RateLimit-Reset` describe the active budget on EVERY
  response (200, 4xx, 429) — they're an ongoing client-
  facing control surface, not a punishment-only signal.
- `Retry-After` remains the special-case field for 429s.

Invariants:
- A module-scope helper `_burst_rate_headers(user_id,
  bucket)` returns the IETF triple as a dict reflecting
  the post-call bucket state (so a client sees
  `Remaining = max - calls-already-made-this-window`).
- A module-scope helper
  `_attach_burst_rate_headers(response, user_id,
  bucket)` mutates a FastAPI Response's headers in
  place. Callsites use this on the success path so
  endpoints returning dicts (FastAPI auto-converts)
  still get the headers.
- Every wizard rate-limited route attaches the headers
  via the helper on the success path: scan, complete,
  generate-phase5, assist, save-progress, reset.
"""
from __future__ import annotations
import inspect


def test_burst_rate_headers_helper_exists():
    """Module-scope helper returns the IETF triple."""
    import server as _s
    fn = getattr(_s, "_burst_rate_headers", None)
    assert fn is not None and callable(fn), (
        "BRAIN-113 regression: server must expose "
        "`_burst_rate_headers(user_id, bucket)` so success "
        "paths can attach the same RateLimit-* triple as "
        "the 429 path."
    )


def test_burst_rate_headers_returns_full_triple():
    """Behavioral: helper returns RateLimit-Limit,
    -Remaining, -Reset with sane numeric values."""
    import server as _s
    headers = _s._burst_rate_headers(user_id=42, bucket="wizard_scan")
    assert isinstance(headers, dict)
    keys = {k.lower() for k in headers}
    for needed in ("ratelimit-limit", "ratelimit-remaining", "ratelimit-reset"):
        assert needed in keys, (
            f"BRAIN-113 regression: helper missing `{needed}` — "
            f"clients can't throttle proactively without the "
            f"full triple."
        )


def test_burst_rate_headers_remaining_decrements():
    """Behavioral: after _check_ai_rate fires once for a
    user (allowed), the helper's Remaining must reflect
    the consumed slot — i.e. Remaining ≤ Limit - 1."""
    import server as _s
    # Use a unique user id so we don't collide with state
    # from other tests.
    uid = 91234567
    bucket = "wizard_scan"
    # Drain any prior state for this synthetic user.
    _s._rate_state.setdefault(bucket, {}).pop(uid, None)
    # Single allowed call.
    blocked = _s._check_ai_rate(uid, bucket=bucket)
    assert blocked is False
    headers = _s._burst_rate_headers(uid, bucket)
    limit = int(headers["RateLimit-Limit"])
    remaining = int(headers["RateLimit-Remaining"])
    assert remaining <= limit - 1, (
        "BRAIN-113 regression: after one allowed call the "
        "helper must report at most Limit-1 remaining. "
        "Otherwise clients can't see budget depletion in "
        "advance — exactly the gap BRAIN-113 closes."
    )
    # Cleanup.
    _s._rate_state.setdefault(bucket, {}).pop(uid, None)


def test_burst_rate_headers_remaining_floor_zero():
    """Behavioral: Remaining must never go negative even
    if the user has somehow exceeded the cap (e.g. from
    a stale state burst). Clients that branch on
    `if remaining < 0` or use uint coercion would
    misbehave."""
    import server as _s
    uid = 91234568
    bucket = "wizard_scan"
    window, max_calls = _s._RATE_BUCKETS[bucket]
    # Synthetically pre-load state with > max_calls fresh
    # timestamps to simulate an over-the-cap state.
    import time
    _now = time.time()
    state = _s._rate_state.setdefault(bucket, {})
    state[uid] = [_now - 0.1] * (max_calls + 5)
    headers = _s._burst_rate_headers(uid, bucket)
    remaining = int(headers["RateLimit-Remaining"])
    assert remaining >= 0, (
        "BRAIN-113 regression: Remaining must clamp at 0 "
        "for over-the-cap state, never go negative."
    )
    state.pop(uid, None)


def test_attach_helper_mutates_response_headers():
    """Behavioral: `_attach_burst_rate_headers(response,
    ...)` mutates `response.headers` in place so FastAPI
    endpoints returning a dict still get the headers
    applied at serialization."""
    import server as _s
    from fastapi import Response
    resp = Response()
    uid = 91234569
    bucket = "wizard_scan"
    _s._rate_state.setdefault(bucket, {}).pop(uid, None)
    _s._check_ai_rate(uid, bucket=bucket)
    _s._attach_burst_rate_headers(resp, uid, bucket)
    # Headers should now contain the triple.
    keys = {k.lower() for k in resp.headers.keys()}
    for needed in ("ratelimit-limit", "ratelimit-remaining", "ratelimit-reset"):
        assert needed in keys, (
            f"BRAIN-113 regression: attach helper failed to "
            f"set `{needed}` on the FastAPI Response."
        )
    _s._rate_state.setdefault(bucket, {}).pop(uid, None)


def test_attach_helper_safe_for_unknown_bucket():
    """Behavioral: an unknown bucket name should not
    raise — defensive callers shouldn't crash a request
    if the bucket map drifts. Headers may be omitted
    rather than wrong."""
    import server as _s
    from fastapi import Response
    resp = Response()
    # Should not raise.
    _s._attach_burst_rate_headers(resp, user_id=1, bucket="nonexistent_bucket_xyz")


def _wizard_endpoint_attaches(endpoint_fn) -> bool:
    src = inspect.getsource(endpoint_fn)
    return "_attach_burst_rate_headers(" in src


def test_wizard_scan_attaches_headers_on_success():
    """Source-level: /api/wizard/scan must attach the
    rate-limit headers to its success-path Response."""
    from server import api_wizard_scan
    assert _wizard_endpoint_attaches(api_wizard_scan), (
        "BRAIN-113 regression: api_wizard_scan must call "
        "`_attach_burst_rate_headers(response, user['id'], "
        "'wizard_scan')` so the 200 carries the same "
        "RateLimit-* triple as the 429 path."
    )


def test_wizard_complete_attaches_headers_on_success():
    from server import api_wizard_complete
    assert _wizard_endpoint_attaches(api_wizard_complete), (
        "BRAIN-113 regression: api_wizard_complete must "
        "call `_attach_burst_rate_headers`."
    )


def test_wizard_phase5_attaches_headers_on_success():
    from server import api_wizard_generate_phase5
    assert _wizard_endpoint_attaches(api_wizard_generate_phase5), (
        "BRAIN-113 regression: api_wizard_generate_phase5 "
        "must call `_attach_burst_rate_headers`."
    )


def test_wizard_assist_attaches_headers_on_success():
    from server import api_wizard_assist
    assert _wizard_endpoint_attaches(api_wizard_assist), (
        "BRAIN-113 regression: api_wizard_assist must "
        "call `_attach_burst_rate_headers`."
    )


def test_wizard_save_progress_attaches_headers_on_success():
    from server import api_wizard_save_progress
    assert _wizard_endpoint_attaches(api_wizard_save_progress), (
        "BRAIN-113 regression: api_wizard_save_progress "
        "must call `_attach_burst_rate_headers`."
    )


def test_wizard_reset_attaches_headers_on_success():
    from server import api_wizard_reset
    assert _wizard_endpoint_attaches(api_wizard_reset), (
        "BRAIN-113 regression: api_wizard_reset must call "
        "`_attach_burst_rate_headers`."
    )
