"""Regression tests for BRAIN-132 (a505): /api/wizard/
complete must accept a client-supplied `Idempotency-Key`
header and replay the original stored response for
retries with the same key. Content fingerprinting
(BRAIN-85) is NOT the same as client-visible retry
safety.

Failure mode (Per Huntova engineering review on
client retry safety + Idempotency-Key contract):

BRAIN-85 (a449) added a content-fingerprint cache
that returns `reused: true` when the same payload
hits twice. That covers "user clicked Complete
twice on the same answers". It does NOT cover:

- Network failure mid-response (server committed,
  client never received the 200). Client retries
  the same payload → BRAIN-85 fingerprint hits →
  returns `reused: true`. Client treats `reused`
  semantically different from `ok`.
- Retry semantics across logical operations. A
  client wants to know "is THIS request a retry of
  one I already sent?" — that's a client-side
  intent, not a content equality.

Standard contract (Stripe / AWS / Google Cloud
guidance): an `Idempotency-Key` header marks one
logical operation. The server stores the resulting
status code + body for a bounded TTL. Subsequent
retries with the same key replay the stored
response verbatim — same status, same body. A new
key with identical content is a new logical
operation.

Invariants:
- Module-scope constant `_IDEMPOTENCY_TTL_SEC`
  (default 14 days, matching BRAIN-101's fingerprint
  TTL). Env-overridable.
- `_IDEMPOTENCY_KEY_MAX_LEN` (default 255) bounds
  opaque keys.
- `_IDEMPOTENCY_CACHE_PER_USER_MAX` (default 50)
  bounds per-user cache size.
- Helper `_idempotency_lookup(user_id, key)` returns
  cached `{status, body}` or None.
- Helper `_idempotency_store(user_id, key, status,
  body)` persists via merge_settings.
- /api/wizard/complete reads `Idempotency-Key`
  header BEFORE the daily-quota check (replays
  must not consume quota). Cache hit → replay; miss
  → run normal flow + store successful response.
- Only 2xx successes are cached.
"""
from __future__ import annotations
import inspect


def test_idempotency_ttl_constant_exists():
    """TTL bound for stored responses."""
    import server as _s
    val = getattr(_s, "_IDEMPOTENCY_TTL_SEC", None)
    assert val is not None, (
        "BRAIN-132 regression: server must expose "
        "`_IDEMPOTENCY_TTL_SEC`."
    )
    assert isinstance(val, int) and val > 0
    # 1 hour minimum, 30 days max.
    assert 3600 <= val <= 30 * 24 * 3600


def test_idempotency_key_max_len_constant_exists():
    """Bound on opaque keys."""
    import server as _s
    val = getattr(_s, "_IDEMPOTENCY_KEY_MAX_LEN", None)
    assert val is not None
    assert isinstance(val, int) and val >= 32


def test_idempotency_cache_per_user_max_constant_exists():
    """Per-user cache size cap."""
    import server as _s
    val = getattr(_s, "_IDEMPOTENCY_CACHE_PER_USER_MAX", None)
    assert val is not None
    assert isinstance(val, int) and val >= 5


def test_idempotency_lookup_helper_exists():
    """Module-scope async helper for cache lookup."""
    import server as _s
    fn = getattr(_s, "_idempotency_lookup", None)
    assert fn is not None and callable(fn)


def test_idempotency_store_helper_exists():
    """Module-scope async helper for cache write."""
    import server as _s
    fn = getattr(_s, "_idempotency_store", None)
    assert fn is not None and callable(fn)


def test_idempotency_key_clean_validates():
    """Helper rejects unusable keys (empty, too long,
    non-printable)."""
    import server as _s
    assert _s._idempotency_key_clean("") is None
    assert _s._idempotency_key_clean(None) is None
    assert _s._idempotency_key_clean("x" * 1000) is None  # too long
    assert _s._idempotency_key_clean("good-key-123") == "good-key-123"
    # Non-printable chars rejected.
    assert _s._idempotency_key_clean("evil\x00key") is None
    assert _s._idempotency_key_clean("evil\nkey") is None


def test_complete_handler_reads_idempotency_header():
    """Source-level: api_wizard_complete reads the
    Idempotency-Key header and consults the lookup
    helper."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    has_header_read = (
        "idempotency-key" in src.lower()
        or "Idempotency-Key" in src
    )
    assert has_header_read, (
        "BRAIN-132 regression: api_wizard_complete must "
        "read the Idempotency-Key request header."
    )
    assert "_idempotency_lookup(" in src, (
        "BRAIN-132 regression: api_wizard_complete must "
        "call _idempotency_lookup before running the "
        "normal flow."
    )


def test_complete_handler_stores_after_success():
    """Source-level: api_wizard_complete calls
    _idempotency_store after a successful response so
    subsequent retries replay it."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    assert "_idempotency_store(" in src


def test_idempotency_lookup_returns_none_for_missing_key():
    """Behavioral: looking up a non-existent key
    returns None (no cached response)."""
    import server as _s
    import asyncio
    out = asyncio.run(_s._idempotency_lookup(99999, "definitely-not-cached-key"))
    assert out is None


def test_idempotency_lookup_returns_none_for_invalid_key():
    """Behavioral: empty / malformed keys return None
    without ever touching the DB."""
    import server as _s
    import asyncio
    assert asyncio.run(_s._idempotency_lookup(99999, "")) is None
    assert asyncio.run(_s._idempotency_lookup(99999, None)) is None


def test_idempotency_lookup_precedes_quota_check():
    """Source-level: the lookup must come BEFORE the
    daily-quota check in api_wizard_complete. Replays
    must not consume quota — that would penalize
    legitimate retries with a fresh quota deduction
    every time."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    lookup_idx = src.find("_idempotency_lookup(")
    quota_idx = src.find("_read_paid_quota_async(")
    if quota_idx == -1:
        quota_idx = src.find("_check_paid_endpoint_quota_async(")
    assert lookup_idx >= 0
    assert quota_idx >= 0
    assert lookup_idx < quota_idx, (
        "BRAIN-132 regression: idempotency lookup must "
        "precede the daily-quota check. Replays must not "
        "consume quota."
    )
