"""Regression tests for BRAIN-142 (a523): /api/chat
dispatcher hardening parity with the wizard surface —
body-byte cap, dedicated rate-limit bucket, RateLimit-*
headers, and Idempotency-Key replay contract.

Failure mode (Per Huntova engineering review on
adjacent-AI-surface parity):

`/api/chat` is the dashboard brain dispatcher — it
parses free text, can dispatch server-side actions
(settings mutations, lead writes, recipes), and
SPENDS BYOK tokens on every call. Pre-fix it had:

- `_check_ai_rate(user["id"])` with the DEFAULT bucket,
  not a chat-specific one. Default bucket is shared
  with every other "AI" callsite.
- NO `_enforce_body_byte_cap` — direct `request.json()`
  on arbitrary-sized bodies.
- NO `_attach_burst_rate_headers` on success.
- NO `_rate_limit_429` on burst — uses ad-hoc bare 429
  shape `{action:"answer", text:"..."}`.
- NO Idempotency-Key support — a lost-response retry
  re-spends tokens on the same logical operation.

Per Huntova engineering review on adjacent-AI-surface
parity: any endpoint that accepts user-authored chat
payloads and can spend model tokens must enforce
pre-parse byte cap, per-endpoint rate limit, RateLimit-*
headers on success + 429, and Idempotency-Key replay.
The wizard surface got this; chat has the same cost
profile and must match.

Invariants:
- `chat` bucket exists in `_RATE_BUCKETS` with sane
  numbers.
- `api_chat` calls `_enforce_body_byte_cap` BEFORE
  `request.json()`.
- `api_chat` calls `_check_ai_rate(user_id, bucket="chat")`
  (not the default).
- 429 path uses `_rate_limit_429` (not bare ad-hoc).
- Success path attaches RateLimit-* headers via
  `_attach_burst_rate_headers`.
- Idempotency-Key header lookup at handler entry +
  store on success.
"""
from __future__ import annotations
import inspect


def test_chat_bucket_exists_in_rate_buckets():
    """Module-scope: `chat` bucket configured."""
    import server as _s
    buckets = getattr(_s, "_RATE_BUCKETS", None)
    assert buckets is not None
    assert "chat" in buckets, (
        "BRAIN-142 regression: `_RATE_BUCKETS` must "
        "have a dedicated `chat` entry. Sharing the "
        "default `ai` bucket means chat traffic "
        "competes with every other AI callsite for "
        "the same per-user budget."
    )
    window, max_calls = buckets["chat"]
    assert isinstance(window, int) and window > 0
    assert isinstance(max_calls, int) and max_calls > 0
    # Sanity: chat is interactive — sub-30 calls/min
    # is too tight for a chat conversation.
    assert max_calls >= 20


def test_chat_handler_enforces_byte_cap():
    """Source-level: api_chat calls
    `_enforce_body_byte_cap` BEFORE `request.json()`."""
    from server import api_chat
    src = inspect.getsource(api_chat)
    assert "_enforce_body_byte_cap(" in src, (
        "BRAIN-142 regression: api_chat must call "
        "`_enforce_body_byte_cap`. Without it, /api/chat "
        "is the next oversize-body ingress vector."
    )
    cap_idx = src.find("_enforce_body_byte_cap(")
    json_idx = src.find("request.json(")
    assert cap_idx >= 0 and json_idx >= 0
    assert cap_idx < json_idx, (
        "BRAIN-142 regression: byte-cap must precede "
        "`request.json()` so an oversize body never "
        "pays parse cost."
    )


def test_chat_handler_uses_chat_bucket():
    """Source-level: `_check_ai_rate` is called with
    `bucket="chat"` — not the default."""
    from server import api_chat
    src = inspect.getsource(api_chat)
    assert '_check_ai_rate(user["id"], bucket="chat")' in src or (
        "_check_ai_rate(" in src and '"chat"' in src
    ), (
        "BRAIN-142 regression: api_chat must use the "
        "`chat` bucket, not the default `ai`. Default "
        "bucket sharing causes cross-callsite starvation."
    )


def test_chat_handler_uses_rate_limit_429_helper():
    """Source-level: 429 path uses the shared
    `_rate_limit_429` helper for IETF RateLimit-*
    headers + `Retry-After`."""
    from server import api_chat
    src = inspect.getsource(api_chat)
    assert "_rate_limit_429(" in src, (
        "BRAIN-142 regression: api_chat 429 path must "
        "use `_rate_limit_429` for header parity with "
        "the wizard surface (BRAIN-112)."
    )


def test_chat_handler_attaches_rate_headers_on_success():
    """Source-level: success path attaches RateLimit-*
    triple via `_attach_burst_rate_headers` (BRAIN-113)."""
    from server import api_chat
    src = inspect.getsource(api_chat)
    assert "_attach_burst_rate_headers(" in src, (
        "BRAIN-142 regression: api_chat must attach "
        "RateLimit-* headers on success so clients can "
        "throttle proactively (BRAIN-113 contract)."
    )


def test_chat_handler_supports_idempotency_key():
    """Source-level: handler reads `Idempotency-Key` +
    consults the lookup helper at entry."""
    from server import api_chat
    src = inspect.getsource(api_chat)
    has_header_read = (
        "idempotency-key" in src.lower()
        or "Idempotency-Key" in src
    )
    assert has_header_read, (
        "BRAIN-142 regression: api_chat must read the "
        "`Idempotency-Key` request header — chat "
        "actions can spend tokens, so retry safety "
        "matters."
    )
    assert "_idempotency_lookup(" in src, (
        "BRAIN-142 regression: api_chat must call "
        "`_idempotency_lookup` so a retry with the same "
        "key replays the original response."
    )


def test_chat_handler_stores_idempotent_response_on_success():
    """Source-level: success path persists the response
    under the client-supplied key for replay."""
    from server import api_chat
    src = inspect.getsource(api_chat)
    assert "_idempotency_store(" in src, (
        "BRAIN-142 regression: api_chat must call "
        "`_idempotency_store` on the success path."
    )
