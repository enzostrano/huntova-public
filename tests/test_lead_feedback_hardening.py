"""Regression tests for BRAIN-139 (a518): adjacent-AI-surface
parity for `/api/lead-feedback`. The endpoint accepts user-
authored brain-shaping input AND can trigger DNA refinement +
learning-profile rebuild — same risk class as the wizard
mutating endpoints, so it must enforce the same three front-
door guarantees as `/api/wizard/complete`:

1. Bounded body size (BRAIN-117 byte-cap shared constant
   `_WIZARD_BODY_BYTES_MAX`).
2. Bounded burst rate via the BRAIN-91 `_check_ai_rate`
   bucket system, with a dedicated `lead_feedback` bucket.
3. Success-path RateLimit-* headers (BRAIN-113
   `_attach_burst_rate_headers`).

Failure mode (Per Huntova engineering review on adjacent-
AI-surface parity):

`/api/lead-feedback` had only a custom 5-min DB-windowed
counter (10 feedback per 5 min) — useful as a daily-quota
class limit, but it doesn't engage until AFTER the JSON
parse + auth + ownership check, and it has no
`Retry-After` / `RateLimit-*` hints. The endpoint also
read `await request.json()` directly with no top-level
byte cap, so a 10 MB POST forced full parse before any
budget check. The closed-schema check was ad-hoc
(lead_id + signal validation, reason capped at 500 chars
post-parse). After other surfaces (wizard endpoints in
BRAIN-117/118 + agent_control in BRAIN-122) all
adopted the same front-door pattern, this endpoint
became the easiest remaining adjacent surface for
resource exhaustion.

Invariants:
- `lead_feedback` bucket exists in `_RATE_BUCKETS` with
  a sane (window, max) — burst-class, NOT replacing the
  existing 5-min DB counter.
- `api_lead_feedback` calls `_check_ai_rate(user_id,
  bucket="lead_feedback")` and returns
  `_rate_limit_429(...)` on block.
- `api_lead_feedback` calls
  `_attach_burst_rate_headers(response, ...)` on the
  success path so clients see the budget proactively.
- `api_lead_feedback` calls
  `_enforce_body_byte_cap(request, _WIZARD_BODY_BYTES_MAX)`
  BEFORE `await request.json()`.
- The existing 5-min DB-windowed counter is preserved
  (complementary daily-quota class limit) — we don't
  delete the SQL row-count check.
"""
from __future__ import annotations
import asyncio
import inspect
import json


def test_lead_feedback_bucket_registered():
    """`lead_feedback` bucket exists in _RATE_BUCKETS with
    a sane (window, max) — feedback is cheap but not
    unlimited."""
    import server as _s
    cfg = _s._RATE_BUCKETS.get("lead_feedback")
    assert cfg is not None, (
        "BRAIN-139 regression: _RATE_BUCKETS must register "
        "a `lead_feedback` bucket so `_check_ai_rate(user, "
        "bucket='lead_feedback')` and "
        "`_attach_burst_rate_headers(..., 'lead_feedback')` "
        "have a config to read."
    )
    window, max_calls = cfg
    assert isinstance(window, int) and window > 0
    assert isinstance(max_calls, int) and max_calls > 0
    # Sanity: feedback is cheap, but not unlimited. A bucket
    # this small (e.g. 60s / 1) would break a normal
    # rapid-feedback session; this large (e.g. 60s / 10000)
    # would be a non-defense.
    assert 1 <= window <= 600
    assert 1 <= max_calls <= 200


def test_handler_calls_byte_cap():
    """Source-level: handler invokes the shared body-cap
    helper with the shared constant."""
    from server import api_lead_feedback
    src = inspect.getsource(api_lead_feedback)
    assert "_enforce_body_byte_cap(" in src, (
        "BRAIN-139 regression: api_lead_feedback must call "
        "`_enforce_body_byte_cap` so a 10 MB POST is "
        "rejected in microseconds without paying parse "
        "cost. Adjacent-surface parity with wizard endpoints."
    )
    assert "_WIZARD_BODY_BYTES_MAX" in src, (
        "BRAIN-139 regression: api_lead_feedback must use "
        "the shared `_WIZARD_BODY_BYTES_MAX` constant — "
        "operators tuning the cap should change one place, "
        "not two."
    )


def test_byte_cap_precedes_json_parse():
    """Source-level: byte-cap runs BEFORE `await request.json()`
    so an oversize body short-circuits without parse cost."""
    from server import api_lead_feedback
    src = inspect.getsource(api_lead_feedback)
    cap_idx = src.find("_enforce_body_byte_cap(")
    json_idx = src.find("request.json(")
    assert cap_idx >= 0 and json_idx >= 0
    assert cap_idx < json_idx, (
        "BRAIN-139 regression: byte-cap must precede "
        "`request.json()` in api_lead_feedback."
    )


def test_handler_calls_burst_rate_check():
    """Source-level: handler invokes BRAIN-91
    `_check_ai_rate` with the lead_feedback bucket."""
    from server import api_lead_feedback
    src = inspect.getsource(api_lead_feedback)
    assert "_check_ai_rate(" in src, (
        "BRAIN-139 regression: api_lead_feedback must call "
        "`_check_ai_rate` for burst-class rate limiting "
        "(complements the existing 5-min DB-windowed "
        "counter, which is daily-quota class)."
    )
    assert 'bucket="lead_feedback"' in src or "bucket='lead_feedback'" in src, (
        "BRAIN-139 regression: api_lead_feedback must pass "
        "`bucket='lead_feedback'` so it doesn't share the "
        "default 'ai' bucket counter with unrelated AI "
        "surfaces."
    )


def test_handler_returns_rate_limit_429_helper():
    """Source-level: handler returns `_rate_limit_429(...)`
    on burst block so the client gets `Retry-After` +
    `RateLimit-*` headers, not a bare JSONResponse."""
    from server import api_lead_feedback
    src = inspect.getsource(api_lead_feedback)
    assert "_rate_limit_429(" in src, (
        "BRAIN-139 regression: api_lead_feedback must call "
        "`_rate_limit_429` on the burst-block path so the "
        "client receives Retry-After + RateLimit-* headers "
        "(IETF draft-ietf-httpapi-ratelimit-headers)."
    )


def test_handler_attaches_burst_rate_headers_on_success():
    """Source-level: handler invokes
    `_attach_burst_rate_headers` so the success-path
    response carries the same RateLimit-* triple as the
    429 path. Without this, multi-tab and high-latency
    clients only learn the budget by accidentally
    exceeding it."""
    from server import api_lead_feedback
    src = inspect.getsource(api_lead_feedback)
    assert "_attach_burst_rate_headers(" in src, (
        "BRAIN-139 regression: api_lead_feedback must call "
        "`_attach_burst_rate_headers(response, ..., "
        "'lead_feedback')` on the success path so clients "
        "throttle proactively."
    )
    assert '"lead_feedback"' in src or "'lead_feedback'" in src, (
        "BRAIN-139 regression: api_lead_feedback must pass "
        "the matching bucket name to "
        "`_attach_burst_rate_headers`."
    )


def test_handler_signature_takes_response_param():
    """Source-level: handler's signature must include a
    `response: Response` parameter so FastAPI provides a
    Response instance for `_attach_burst_rate_headers` to
    mutate. Without this parameter the dict-return path
    creates a fresh Response *after* the handler returns,
    too late to mutate."""
    from server import api_lead_feedback
    sig = inspect.signature(api_lead_feedback)
    assert "response" in sig.parameters, (
        "BRAIN-139 regression: api_lead_feedback must "
        "accept a `response: Response` parameter so "
        "RateLimit-* headers can be attached on the "
        "success path."
    )


def test_legacy_5min_db_counter_preserved():
    """Source-level: the existing 5-min DB-windowed
    counter is preserved. Burst + daily-quota class
    limits are complementary; deleting the old check
    while adding the new one would weaken the defense."""
    from server import api_lead_feedback
    src = inspect.getsource(api_lead_feedback)
    assert "lead_feedback WHERE user_id" in src, (
        "BRAIN-139 regression: the legacy 5-min DB-windowed "
        "counter (10 feedback per 5 min) must remain — it "
        "is the daily-quota class limit, complementary to "
        "the BRAIN-91 burst bucket added in this BRAIN. "
        "Deleting it would leave only the per-minute "
        "guard, allowing a slow-burn drain that stays "
        "under the burst cap."
    )


# ── Behavioral tests ──

class _StubRequest:
    """Minimal Starlette-Request shape for unit-testing
    `_enforce_body_byte_cap` directly. Mirrors the stub
    used in test_wizard_payload_byte_cap.py so the cap
    helper has consistent contract coverage across
    surfaces."""

    def __init__(self, content_length, body_bytes):
        self._headers = {}
        if content_length is not None:
            self._headers["content-length"] = content_length
        self._body = body_bytes

    @property
    def headers(self):
        return self._headers

    async def body(self):
        return self._body


def _run_async(coro):
    return asyncio.run(coro)


def test_oversize_body_returns_413_before_json_parse():
    """Behavioral: a body that exceeds
    `_WIZARD_BODY_BYTES_MAX` — declared via
    Content-Length — gets a 413 with the standard
    payload_too_large shape, immediately, without ever
    awaiting body()."""
    import server as _s
    cap = _s._WIZARD_BODY_BYTES_MAX
    # Construct a stub request that LIES about its body —
    # declares content-length above cap but actual body is
    # empty. The cap helper rejects on the declared value
    # alone, never reading the body. This is what protects
    # the lead-feedback endpoint from a 10 MB POST.
    req = _StubRequest(content_length=str(cap + 1), body_bytes=b"")
    body, resp = _run_async(_s._enforce_body_byte_cap(req, cap))
    assert body is None
    assert resp is not None
    assert resp.status_code == 413
    payload = json.loads(resp.body)
    assert payload.get("error_kind") == "payload_too_large"
    assert payload.get("max_bytes") == cap


def test_rapid_fire_fires_429_with_proper_headers():
    """Behavioral: drive the BRAIN-91 burst limiter
    directly with a synthetic user_id. After exhausting
    the `lead_feedback` bucket, the next call returns
    True (block); building the 429 response via
    `_rate_limit_429` carries Retry-After + RateLimit-*
    headers."""
    import server as _s
    bucket = "lead_feedback"
    window, max_calls = _s._RATE_BUCKETS[bucket]
    # Use a synthetic user_id well outside any real range
    # so this test doesn't pollute production state.
    uid = -918273645

    # Reset the bucket state for this user (test-isolated;
    # the legacy view exposes pop, but we go direct on the
    # internal dict so we don't rely on it).
    with _s._ai_rate_lock:
        _s._rate_state.setdefault(bucket, {}).pop(uid, None)

    # Burn the budget.
    for i in range(max_calls):
        blocked = _s._check_ai_rate(uid, bucket=bucket)
        assert not blocked, f"unexpected block at call {i+1}/{max_calls}"

    # Next call must block.
    assert _s._check_ai_rate(uid, bucket=bucket) is True, (
        "BRAIN-139 regression: lead_feedback bucket must "
        "block once `max_calls` is exhausted within the "
        "window. If this passes — the bucket isn't wired "
        "up to `_check_ai_rate` correctly."
    )

    # Build the 429 the way the handler does and verify
    # the headers contract.
    resp = _s._rate_limit_429(uid, bucket, "Too many feedback submissions. Wait a moment.")
    assert resp.status_code == 429
    assert resp.headers.get("Retry-After") == str(int(window))
    assert resp.headers.get("RateLimit-Limit") == str(int(max_calls))
    assert resp.headers.get("RateLimit-Remaining") == "0"
    assert resp.headers.get("RateLimit-Reset") == str(int(window))

    # Cleanup — leave global state pristine for the next
    # test run.
    with _s._ai_rate_lock:
        _s._rate_state.setdefault(bucket, {}).pop(uid, None)


def test_attach_burst_rate_headers_writes_triple():
    """Behavioral: success-path helper writes the same
    RateLimit-* triple onto the Response. With used=0
    (fresh user), Remaining == max_calls; the triple is
    visible to the client."""
    import server as _s
    from fastapi import Response

    bucket = "lead_feedback"
    window, max_calls = _s._RATE_BUCKETS[bucket]
    uid = -918273646

    # Reset state.
    with _s._ai_rate_lock:
        _s._rate_state.setdefault(bucket, {}).pop(uid, None)

    resp = Response()
    _s._attach_burst_rate_headers(resp, uid, bucket)
    assert resp.headers.get("RateLimit-Limit") == str(int(max_calls))
    # Fresh user, no calls — full budget remaining.
    assert resp.headers.get("RateLimit-Remaining") == str(int(max_calls))
    assert resp.headers.get("RateLimit-Reset") == str(int(window))

    # Cleanup.
    with _s._ai_rate_lock:
        _s._rate_state.setdefault(bucket, {}).pop(uid, None)
