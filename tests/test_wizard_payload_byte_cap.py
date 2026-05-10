"""Regression tests for BRAIN-117 (a486): mutating wizard
endpoints must enforce a top-level request-body byte
cap. OWASP API4:2023 (unrestricted resource consumption)
+ general resource-consumption guidance: key-count caps
and list-count caps don't replace a byte ceiling.

Failure mode (Per Huntova engineering review on
unrestricted resource consumption):

BRAIN-98 (a463) caps `_wizard_answers` at 150 keys.
BRAIN-102 (a471) caps `_knowledge` at 50 items. BRAIN-103
(a472) caps `_phase5_questions` at 5 items. These all
constrain shape, not size. A client can still send a
JSON body with very few keys but enormous values:

    { "outreach_tone": "<10 MB blob>" }

That passes the BRAIN-73 closed-schema check (it's a
known field), passes BRAIN-98 (only 1 key), passes
BRAIN-13 per-field clipping (which truncates AFTER
parsing), but still:
- Forces the server to allocate + parse a 10 MB JSON
  string.
- Forces merge_settings to load + re-serialize the row
  including the giant value, even briefly.
- Triggers BYOK spend if the value flows into a prompt
  before per-field clipping kicks in.

Standard fix: a hard top-level byte cap enforced
BEFORE parse, before merge, before any expensive
coercion. The cap is generous enough that legitimate
wizard payloads (which fit comfortably in 64 KiB)
never bump it, but tight enough that a 10 MB POST
returns 413 in microseconds.

Invariants:
- Module-scope constant `_WIZARD_BODY_BYTES_MAX`
  (default 262144 = 256 KiB, env-overridable via
  `HV_WIZARD_BODY_BYTES_MAX`).
- Helper `_enforce_body_byte_cap(request, max_bytes)`
  returns either (body_bytes, None) on OK, or
  (None, JSONResponse(413, ...)) on overrun.
- Endpoint flow rejects via Content-Length first
  (cheap, no body read) and falls through to actual-
  body-length check (catches lying or missing
  Content-Length on chunked uploads).
- 413 body shape: `{ok:false, error_kind:
  "payload_too_large", max_bytes: N}` — predictable
  for clients.
- `api_wizard_save_progress` and `api_wizard_complete`
  call the helper BEFORE any `request.json()` /
  merge_settings work.
"""
from __future__ import annotations
import asyncio
import inspect


def test_body_bytes_max_constant_exists():
    """Module-scope constant defines the cap."""
    import server as _s
    val = getattr(_s, "_WIZARD_BODY_BYTES_MAX", None)
    assert val is not None, (
        "BRAIN-117 regression: server must expose "
        "`_WIZARD_BODY_BYTES_MAX` at module scope."
    )
    assert isinstance(val, int) and val > 0
    # Sanity: the cap is generous enough to fit a real
    # wizard payload (the BRAIN-13 per-field clip is 4 KB,
    # times ~10 fields = 40 KB worst case + overhead).
    # 64 KiB minimum; 256 KiB default.
    assert val >= 65536, "cap too aggressive for real payloads"
    # And not absurdly large.
    assert val <= 16 * 1024 * 1024, "cap too lax to count as a defense"


def test_enforce_body_byte_cap_helper_exists():
    """Module-scope helper does the actual work."""
    import server as _s
    fn = getattr(_s, "_enforce_body_byte_cap", None)
    assert fn is not None and callable(fn), (
        "BRAIN-117 regression: server must expose "
        "`_enforce_body_byte_cap(request, max_bytes)` "
        "returning (body_bytes, None) on OK or "
        "(None, JSONResponse) on overrun."
    )


class _StubRequest:
    """Minimal Starlette-Request shape for unit testing
    the body-cap helper without spinning up a server."""

    def __init__(self, content_length: str | None, body_bytes: bytes):
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


def test_helper_rejects_via_declared_content_length():
    """Behavioral: a Content-Length header that exceeds
    the cap is rejected immediately, without ever
    awaiting body()."""
    import server as _s
    cap = 1024
    # Construct a stub request that LIES about its body —
    # declares content-length above cap but actual body is
    # empty. The helper should reject on the declared
    # value alone, never reading the body.
    req = _StubRequest(content_length=str(cap + 1), body_bytes=b"")
    body, resp = _run_async(_s._enforce_body_byte_cap(req, cap))
    assert body is None
    assert resp is not None
    assert resp.status_code == 413
    # Body shape contract.
    import json
    payload = json.loads(resp.body)
    assert payload.get("error_kind") == "payload_too_large"
    assert payload.get("max_bytes") == cap


def test_helper_rejects_via_actual_body_length():
    """Behavioral: a missing Content-Length header (or
    a lying one that under-declares) — the helper falls
    through to actual body() length and rejects there."""
    import server as _s
    cap = 1024
    big_body = b"x" * (cap + 100)
    req = _StubRequest(content_length=None, body_bytes=big_body)
    body, resp = _run_async(_s._enforce_body_byte_cap(req, cap))
    assert body is None
    assert resp is not None
    assert resp.status_code == 413


def test_helper_accepts_under_cap():
    """Behavioral: a body within the cap returns
    (body_bytes, None)."""
    import server as _s
    cap = 1024
    payload = b'{"answers": {"outreach_tone": "warm"}}'
    req = _StubRequest(content_length=str(len(payload)), body_bytes=payload)
    body, resp = _run_async(_s._enforce_body_byte_cap(req, cap))
    assert resp is None
    assert body == payload


def test_helper_handles_invalid_content_length_header():
    """Behavioral: a malformed Content-Length string
    must not crash — fall through to actual body
    measurement."""
    import server as _s
    cap = 1024
    payload = b'{}'
    req = _StubRequest(content_length="not-a-number", body_bytes=payload)
    body, resp = _run_async(_s._enforce_body_byte_cap(req, cap))
    assert resp is None
    assert body == payload


def test_save_progress_enforces_byte_cap():
    """Source-level: api_wizard_save_progress calls the
    helper BEFORE any json parsing or merge work."""
    from server import api_wizard_save_progress
    src = inspect.getsource(api_wizard_save_progress)
    assert "_enforce_body_byte_cap(" in src, (
        "BRAIN-117 regression: api_wizard_save_progress "
        "must call `_enforce_body_byte_cap(request, "
        "_WIZARD_BODY_BYTES_MAX)` before any json/merge "
        "work — that's the OWASP-recommended early "
        "rejection point."
    )


def test_complete_enforces_byte_cap():
    """Source-level: api_wizard_complete calls the
    helper BEFORE any json parsing or merge work."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    assert "_enforce_body_byte_cap(" in src, (
        "BRAIN-117 regression: api_wizard_complete must "
        "call `_enforce_body_byte_cap`."
    )


def test_byte_cap_check_precedes_request_json():
    """Source-level: the byte-cap check must run BEFORE
    `await request.json()` so an oversize body short-
    circuits without paying parse cost."""
    from server import api_wizard_save_progress
    src = inspect.getsource(api_wizard_save_progress)
    cap_idx = src.find("_enforce_body_byte_cap(")
    json_idx = src.find("request.json(")
    assert cap_idx >= 0
    assert json_idx >= 0
    assert cap_idx < json_idx, (
        "BRAIN-117 regression: byte-cap check must come "
        "BEFORE request.json() so we don't pay parse "
        "cost on a 10 MB rejection."
    )
