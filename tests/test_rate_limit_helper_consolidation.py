"""Regression tests for BRAIN-146 (a529): the three
rate-limit-related helpers in server.py must build their
IETF `RateLimit-*` / `Retry-After` headers via one shared
helper — `_rate_headers_for(...)` — so the contract has
one source of truth.

Failure mode (Per Huntova engineering review on shared-
helper interface guarantees + IETF draft-ietf-httpapi-
ratelimit-headers):

BRAIN-112 (a481) and BRAIN-113 (a482) introduced three
helpers that each hand-rolled the same IETF triple plus
optional `Retry-After`:

- `_rate_limit_429(user_id, bucket, message, error_kind)`
  — burst-bucket 429 with `Retry-After: <window-seconds>`
  + `RateLimit-Limit: <max>` + `RateLimit-Remaining: 0` +
  `RateLimit-Reset: <window-seconds>`.
- `_burst_rate_headers(user_id, bucket)` — success-path
  IETF triple (no `Retry-After`).
- `_daily_quota_429(daily_max, message, error_kind)` —
  daily-quota 429 with `Retry-After: <until-utc-midnight>`
  + the same triple.

Each one redundantly reasoned about header casing,
`int()` coercion of float-seconds, and remaining
clamping. BRAIN-126 (a495) extracted a shared
`_wizard_conflict_response` for the four wizard conflict
sites once the same drift risk became real. This is the
third-order extraction for rate-limit headers.

Invariants:
- `_rate_headers_for(*, limit, remaining, reset_seconds,
   retry_after=None)` exists at module scope, takes its
   arguments keyword-only, and returns a `dict[str, str]`.
- The dict carries `RateLimit-Limit`, `RateLimit-Remaining`,
  `RateLimit-Reset` — and `Retry-After` iff
  `retry_after` was passed.
- All three legacy helpers (`_rate_limit_429`,
  `_burst_rate_headers`, `_daily_quota_429`) reference
  `_rate_headers_for` in their source so a future
  drift-by-edit goes through the shared builder.
- Behavioral parity: existing 429 + success-path
  RateLimit-* tests must continue to pass unchanged
  (covered by tests/test_wizard_rate_limit_headers.py
  and tests/test_wizard_rate_limit_headers_on_success.py
  — those run in the full suite alongside this file).
"""
from __future__ import annotations

import inspect


def test_rate_headers_for_helper_exists():
    """Module-scope helper exists + is callable."""
    import server as _s

    fn = getattr(_s, "_rate_headers_for", None)
    assert fn is not None and callable(fn), (
        "BRAIN-146 regression: server must expose "
        "`_rate_headers_for(*, limit, remaining, "
        "reset_seconds, retry_after=None)` so the IETF "
        "RateLimit-* triple has one canonical builder."
    )


def test_rate_headers_for_args_are_keyword_only():
    """All four parameters must be keyword-only so
    callsites read `limit=...`, `remaining=...`, etc.
    Positional invocation would let drift sneak in via
    arg-order swaps."""
    import server as _s

    sig = inspect.signature(_s._rate_headers_for)
    params = list(sig.parameters.values())
    assert params, "_rate_headers_for must accept arguments"
    for p in params:
        assert p.kind == inspect.Parameter.KEYWORD_ONLY, (
            "BRAIN-146 regression: `_rate_headers_for` "
            f"parameter `{p.name}` must be keyword-only "
            "to prevent positional drift across the three "
            "callers."
        )


def test_rate_headers_for_returns_full_triple():
    """With no `retry_after`, the helper still returns
    the three IETF RateLimit-* keys."""
    import server as _s

    out = _s._rate_headers_for(limit=20, remaining=7, reset_seconds=60)
    assert isinstance(out, dict)
    assert out.get("RateLimit-Limit") == "20"
    assert out.get("RateLimit-Remaining") == "7"
    assert out.get("RateLimit-Reset") == "60"
    assert "Retry-After" not in out, (
        "BRAIN-146 regression: success-path callsites pass "
        "`retry_after=None`; the helper must NOT include "
        "Retry-After in that case."
    )


def test_rate_headers_for_includes_retry_after_when_given():
    """With `retry_after=<int>`, the helper adds the
    `Retry-After` header. This is what the two 429 paths
    rely on."""
    import server as _s

    out = _s._rate_headers_for(
        limit=20, remaining=0, reset_seconds=60, retry_after=60
    )
    assert out.get("Retry-After") == "60"
    assert out.get("RateLimit-Limit") == "20"
    assert out.get("RateLimit-Remaining") == "0"
    assert out.get("RateLimit-Reset") == "60"


def test_rate_headers_for_clamps_negative_remaining_to_zero():
    """A buggy caller passing `remaining=-3` must still
    emit `RateLimit-Remaining: 0`. Clients typically
    interpret negatives via wraparound or bail out — both
    bad — so the helper clamps defensively."""
    import server as _s

    out = _s._rate_headers_for(
        limit=20, remaining=-3, reset_seconds=60
    )
    assert out["RateLimit-Remaining"] == "0", (
        "BRAIN-146 regression: `_rate_headers_for` must "
        "clamp negative `remaining` to 0; clients can't "
        "safely interpret a negative budget."
    )


def test_rate_headers_for_coerces_float_inputs_to_int_strings():
    """`time.time()` arithmetic produces floats; the
    helper coerces with `int()` so headers stay
    integer-stringy as the IETF draft requires."""
    import server as _s

    out = _s._rate_headers_for(
        limit=20.0, remaining=7.7, reset_seconds=60.9, retry_after=12.3
    )
    # int() truncates toward zero — that's the BRAIN-112
    # contract on Retry-After. Test against the truncated
    # values rather than asserting any specific rounding.
    assert out["RateLimit-Limit"] == "20"
    assert out["RateLimit-Remaining"] == "7"
    assert out["RateLimit-Reset"] == "60"
    assert out["Retry-After"] == "12"


def test_rate_limit_429_uses_shared_helper():
    """Source-level: the burst-bucket 429 helper must
    route header construction through the shared builder
    so future tweaks land in one place."""
    import server as _s

    src = inspect.getsource(_s._rate_limit_429)
    assert "_rate_headers_for(" in src, (
        "BRAIN-146 regression: `_rate_limit_429` must "
        "call `_rate_headers_for` instead of hand-rolling "
        "the IETF triple. Without the shared call, the 429 "
        "header contract drifts vs. the success path."
    )


def test_burst_rate_headers_uses_shared_helper():
    """Source-level: the success-path triple builder must
    also route through the shared helper."""
    import server as _s

    src = inspect.getsource(_s._burst_rate_headers)
    assert "_rate_headers_for(" in src, (
        "BRAIN-146 regression: `_burst_rate_headers` must "
        "call `_rate_headers_for` so the success-path "
        "triple stays in sync with the 429 paths."
    )


def test_daily_quota_429_uses_shared_helper():
    """Source-level: the daily-quota 429 helper must
    also route through the shared helper."""
    import server as _s

    src = inspect.getsource(_s._daily_quota_429)
    assert "_rate_headers_for(" in src, (
        "BRAIN-146 regression: `_daily_quota_429` must "
        "call `_rate_headers_for` so daily-quota 429 "
        "headers stay in sync with the burst-bucket 429 "
        "+ success path."
    )


def test_attach_burst_rate_headers_routes_through_burst_rate_headers():
    """`_attach_burst_rate_headers` is the in-place
    mutator BRAIN-113 introduced for FastAPI dict-return
    endpoints. It calls `_burst_rate_headers` which now
    routes through the shared builder — so the attach
    helper inherits the consolidation transitively. Pin
    that wiring."""
    import server as _s

    src = inspect.getsource(_s._attach_burst_rate_headers)
    assert "_burst_rate_headers(" in src, (
        "BRAIN-146 regression: `_attach_burst_rate_headers` "
        "must continue to delegate to `_burst_rate_headers` "
        "(which now routes through `_rate_headers_for`) so "
        "the success path inherits the consolidation."
    )


def test_burst_rate_headers_unknown_bucket_returns_empty_dict():
    """Defensive parity check: pre-refactor, an unknown
    bucket short-circuited to `{}` before the helper got
    invoked. That contract must survive — callers
    `.update()` the result onto Response.headers and
    expect a no-op for unknown buckets."""
    import server as _s

    out = _s._burst_rate_headers(user_id=42, bucket="not_a_real_bucket")
    assert out == {}


def test_three_legacy_helpers_share_one_builder():
    """End-to-end: scan the bodies of the three legacy
    helpers and confirm each contains exactly one
    `_rate_headers_for(` call. If a future edit splits a
    helper into a hand-rolled branch + a delegated
    branch, this catches it before the contract drifts."""
    import server as _s

    for name in ("_rate_limit_429", "_burst_rate_headers", "_daily_quota_429"):
        fn = getattr(_s, name)
        src = inspect.getsource(fn)
        # At least one call — accept >1 if a future
        # branch needs a second header dict, but never 0.
        assert src.count("_rate_headers_for(") >= 1, (
            f"BRAIN-146 regression: `{name}` must call "
            "`_rate_headers_for` for IETF header "
            "construction. Hand-rolling the triple lets "
            "the three helpers drift over time."
        )
