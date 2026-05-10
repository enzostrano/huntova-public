"""Regression tests for BRAIN-144 (a527): team-of-
agents endpoints (`/api/team/seed-defaults`,
`/api/team/{slot}/toggle`) lack rate-limit + byte-cap
hardening — adjacent mutating endpoints that the
"Reseed from brain" button can hit repeatedly.

Failure mode (Per Huntova engineering review on
adjacent-AI-surface parity):

`/api/team/seed-defaults` triggers brain work +
multiple DB writes per call. The "Reseed from brain"
UI button (templates/jarvis.html) fires it. Pre-fix
it had:
- No `_check_ai_rate` bucket → users could mash the
  button.
- No `_enforce_body_byte_cap` → arbitrary bodies
  parse before being read.
- No `_attach_burst_rate_headers` → no client-side
  budget signal.

`/api/team/{slot}/toggle` flips enabled/disabled per
slot. Cheap operation but still mutating + still
needs the rate-limit budget for parity.

Per Huntova engineering review on adjacent-AI-surface
parity (BRAIN-122/142/BRAIN-139): every mutating
endpoint that triggers DB / AI work must enforce the
same three front-door guarantees as the wizard
surface — bounded body size, per-endpoint rate limit,
RateLimit-* headers.

Invariants:
- New `team_seed_defaults` bucket in `_RATE_BUCKETS`
  with sane numbers (60s window, 10 calls — reseeding
  is expensive, doesn't need to fire fast).
- New `team_toggle` bucket (60s window, 30 calls —
  cheap operation, higher cap).
- `api_team_reseed` calls
  `_check_ai_rate(user_id, bucket="team_seed_defaults")`,
  `_rate_limit_429`, `_attach_burst_rate_headers`,
  `_enforce_body_byte_cap`.
- `api_team_toggle` calls
  `_check_ai_rate(user_id, bucket="team_toggle")` +
  `_rate_limit_429` + `_attach_burst_rate_headers`.
"""
from __future__ import annotations
import inspect


def test_team_buckets_exist_in_rate_buckets():
    """Module-scope: `team_seed_defaults` +
    `team_toggle` configured."""
    import server as _s
    buckets = _s._RATE_BUCKETS
    assert "team_seed_defaults" in buckets, (
        "BRAIN-144 regression: `_RATE_BUCKETS` must "
        "have a `team_seed_defaults` bucket."
    )
    assert "team_toggle" in buckets, (
        "BRAIN-144 regression: `_RATE_BUCKETS` must "
        "have a `team_toggle` bucket."
    )


def test_team_seed_defaults_handler_hardened():
    """Source-level: api_team_reseed calls all four
    helpers (rate-check, 429, headers, byte-cap)."""
    from server import api_team_reseed
    src = inspect.getsource(api_team_reseed)
    assert "_check_ai_rate(" in src, (
        "BRAIN-144 regression: api_team_reseed must "
        "call `_check_ai_rate`. Reseed is expensive — "
        "users mashing the button could thrash brain "
        "work."
    )
    assert "_rate_limit_429(" in src, (
        "BRAIN-144 regression: api_team_reseed must "
        "use `_rate_limit_429` on the 429 path."
    )
    assert "_attach_burst_rate_headers(" in src, (
        "BRAIN-144 regression: api_team_reseed success "
        "path must attach RateLimit-* headers."
    )
    assert "_enforce_body_byte_cap(" in src, (
        "BRAIN-144 regression: api_team_reseed must "
        "byte-cap the body before parse."
    )


def test_team_seed_uses_dedicated_bucket():
    """Source-level: uses `team_seed_defaults` bucket,
    not the default."""
    from server import api_team_reseed
    src = inspect.getsource(api_team_reseed)
    assert '"team_seed_defaults"' in src or "'team_seed_defaults'" in src, (
        "BRAIN-144 regression: api_team_reseed must "
        "use the dedicated `team_seed_defaults` bucket."
    )


def test_team_seed_byte_cap_precedes_json_parse():
    """Source-level: byte-cap precedes `request.json()`."""
    from server import api_team_reseed
    src = inspect.getsource(api_team_reseed)
    cap_idx = src.find("_enforce_body_byte_cap(")
    json_idx = src.find("request.json(")
    assert cap_idx >= 0
    if json_idx >= 0:
        assert cap_idx < json_idx


def test_team_toggle_handler_hardened():
    """Source-level: api_team_toggle calls rate-limit
    + 429 helpers + headers (no byte cap needed —
    toggle has no body)."""
    from server import api_team_toggle
    src = inspect.getsource(api_team_toggle)
    assert "_check_ai_rate(" in src, (
        "BRAIN-144 regression: api_team_toggle must "
        "call `_check_ai_rate`."
    )
    assert "_rate_limit_429(" in src, (
        "BRAIN-144 regression: api_team_toggle must "
        "use `_rate_limit_429` on the 429 path."
    )
    assert "_attach_burst_rate_headers(" in src, (
        "BRAIN-144 regression: api_team_toggle success "
        "path must attach RateLimit-* headers."
    )


def test_team_toggle_uses_dedicated_bucket():
    """Source-level: api_team_toggle uses `team_toggle`."""
    from server import api_team_toggle
    src = inspect.getsource(api_team_toggle)
    assert '"team_toggle"' in src or "'team_toggle'" in src
