"""Regression tests for BRAIN-145 (a528): `/api/memory`
POST + `/api/memory/{memory_id}` DELETE lack the
BRAIN-91/112/113/117 contract — adjacent mutating
endpoints that accept user-authored memory items.

Failure mode (Per Huntova engineering review on
adjacent-AI-surface parity):

`POST /api/memory` accepts a user-authored memory entry
(key + value) which is then surfaced to the chat
SYSTEM_PROMPT. Pre-fix it had:
- A legacy default-bucket `_check_ai_rate(user_id)` call
  (no dedicated bucket) → manual memory writes fought
  for budget with the chat / wizard surfaces.
- A bare `JSONResponse(429)` instead of
  `_rate_limit_429` → no Retry-After / RateLimit-*
  headers on the 429 path.
- No `_enforce_body_byte_cap` → arbitrary JSON bodies
  parsed before being read; rogue scripts could push
  multi-MB blobs to bloat the table or poison the
  SYSTEM_PROMPT injection.
- No `_attach_burst_rate_headers` on the success path
  → no client-side budget signal.

`DELETE /api/memory/{memory_id}` archives a memory row.
Cheap operation but still mutating + still needs the
rate-limit budget for parity.

Per Huntova engineering review on adjacent-AI-surface
parity (BRAIN-122/139/142/144): every mutating endpoint
that triggers DB / AI work must enforce the same three
front-door guarantees as the wizard surface — bounded
body size, per-endpoint rate limit, RateLimit-*
headers.

Invariants:
- New `memory` bucket in `_RATE_BUCKETS` with sane
  numbers (60s window, modest cap — manual memory
  writes are interactive, not high-frequency).
- `api_memory_record` calls
  `_check_ai_rate(user_id, bucket="memory")`,
  `_rate_limit_429`, `_attach_burst_rate_headers`,
  `_enforce_body_byte_cap`.
- `api_memory_archive` calls
  `_check_ai_rate(user_id, bucket="memory")` +
  `_rate_limit_429` + `_attach_burst_rate_headers`.
"""
from __future__ import annotations
import inspect


def test_memory_bucket_exists_in_rate_buckets():
    """Module-scope: `memory` bucket configured."""
    import server as _s
    buckets = _s._RATE_BUCKETS
    assert "memory" in buckets, (
        "BRAIN-145 regression: `_RATE_BUCKETS` must "
        "have a `memory` bucket."
    )
    window, cap = buckets["memory"]
    assert window > 0 and cap > 0, (
        "BRAIN-145 regression: `memory` bucket must "
        "have positive window + cap."
    )


def test_memory_record_handler_hardened():
    """Source-level: api_memory_record calls all four
    helpers (rate-check, 429, headers, byte-cap)."""
    from server import api_memory_record
    src = inspect.getsource(api_memory_record)
    assert "_check_ai_rate(" in src, (
        "BRAIN-145 regression: api_memory_record must "
        "call `_check_ai_rate`."
    )
    assert "_rate_limit_429(" in src, (
        "BRAIN-145 regression: api_memory_record must "
        "use `_rate_limit_429` on the 429 path — bare "
        "JSONResponse(status_code=429) drops the IETF "
        "Retry-After + RateLimit-* triple."
    )
    assert "_attach_burst_rate_headers(" in src, (
        "BRAIN-145 regression: api_memory_record "
        "success path must attach RateLimit-* headers."
    )
    assert "_enforce_body_byte_cap(" in src, (
        "BRAIN-145 regression: api_memory_record must "
        "byte-cap the body before parse — manual memory "
        "rows feed the chat SYSTEM_PROMPT and must not "
        "accept multi-MB payloads."
    )


def test_memory_record_uses_dedicated_bucket():
    """Source-level: uses `memory` bucket, not the
    default `ai`."""
    from server import api_memory_record
    src = inspect.getsource(api_memory_record)
    assert '"memory"' in src or "'memory'" in src, (
        "BRAIN-145 regression: api_memory_record must "
        "use the dedicated `memory` bucket so it does "
        "not fight for budget with the chat / wizard "
        "surfaces."
    )


def test_memory_record_byte_cap_precedes_json_parse():
    """Source-level: byte-cap precedes
    `request.json()` — otherwise the parse work happens
    before the cap fires."""
    from server import api_memory_record
    src = inspect.getsource(api_memory_record)
    cap_idx = src.find("_enforce_body_byte_cap(")
    json_idx = src.find("request.json(")
    assert cap_idx >= 0
    assert json_idx >= 0
    assert cap_idx < json_idx, (
        "BRAIN-145 regression: byte-cap must run before "
        "request.json() so oversize bodies short-circuit "
        "at the front door."
    )


def test_memory_archive_handler_hardened():
    """Source-level: api_memory_archive calls
    rate-limit + 429 helpers + headers (no byte cap
    needed — DELETE has no body)."""
    from server import api_memory_archive
    src = inspect.getsource(api_memory_archive)
    assert "_check_ai_rate(" in src, (
        "BRAIN-145 regression: api_memory_archive must "
        "call `_check_ai_rate`."
    )
    assert "_rate_limit_429(" in src, (
        "BRAIN-145 regression: api_memory_archive must "
        "use `_rate_limit_429` on the 429 path."
    )
    assert "_attach_burst_rate_headers(" in src, (
        "BRAIN-145 regression: api_memory_archive "
        "success path must attach RateLimit-* headers."
    )


def test_memory_archive_uses_dedicated_bucket():
    """Source-level: api_memory_archive uses `memory`
    bucket — same surface as the record endpoint, so
    they share a budget."""
    from server import api_memory_archive
    src = inspect.getsource(api_memory_archive)
    assert '"memory"' in src or "'memory'" in src, (
        "BRAIN-145 regression: api_memory_archive must "
        "use the dedicated `memory` bucket."
    )


def test_memory_record_drops_legacy_default_bucket():
    """Source-level: the pre-fix code carried a bare
    `_check_ai_rate(user["id"])` (default `ai` bucket)
    plus a bare `JSONResponse(...status_code=429)`.
    Both must be gone — the bucket because we now have
    `memory`, the bare 429 because the contract
    requires `_rate_limit_429` for header parity.
    """
    from server import api_memory_record
    src = inspect.getsource(api_memory_record)
    # Bare JSONResponse(...status_code=429) means the
    # IETF Retry-After + RateLimit-* triple is missing.
    # The migrated code must route 429s through
    # _rate_limit_429 instead.
    assert "status_code=429" not in src, (
        "BRAIN-145 regression: api_memory_record must "
        "not emit a bare JSONResponse(status_code=429); "
        "use `_rate_limit_429` so the response carries "
        "Retry-After + RateLimit-* headers."
    )
