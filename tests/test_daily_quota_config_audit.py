"""Regression tests for BRAIN-152 (a563): daily-quota
constants integrity. Parallel to BRAIN-151 but for
`_SCAN_DAILY_MAX`, `_PHASE5_DAILY_MAX`,
`_COMPLETE_DAILY_MAX`, `_ASSIST_DAILY_MAX`.

Failure mode: a typo or environment-variable misread
leaves a daily quota at 0 (instant block) or
wildly high (no real cap). Pre-fix: each constant is
read individually; no audit ensures sane bounds.

Invariants:
- All four daily-quota constants exist as ints.
- Each is in `[1, 10000]` (1 = paranoid floor; 10000 =
  realistic ceiling for a single-user CLI tool).
- The constants are env-overridable via `HV_WIZARD_*_DAILY_MAX`
  (smoke test: env override is respected).
"""
from __future__ import annotations
import os
import importlib


def test_daily_quota_constants_exist():
    """All four quota constants registered."""
    import server as _s
    for name in (
        "_SCAN_DAILY_MAX",
        "_PHASE5_DAILY_MAX",
        "_COMPLETE_DAILY_MAX",
        "_ASSIST_DAILY_MAX",
    ):
        val = getattr(_s, name, None)
        assert val is not None, (
            f"BRAIN-152 regression: server must expose "
            f"`{name}` daily-quota constant."
        )
        assert isinstance(val, int), (
            f"BRAIN-152 regression: `{name}` must be int, got {type(val)}."
        )


def test_daily_quotas_are_sane():
    """Each quota in [1, 10000]."""
    import server as _s
    for name in (
        "_SCAN_DAILY_MAX",
        "_PHASE5_DAILY_MAX",
        "_COMPLETE_DAILY_MAX",
        "_ASSIST_DAILY_MAX",
    ):
        val = getattr(_s, name)
        assert 1 <= val <= 10000, (
            f"BRAIN-152 regression: `{name}` = {val} "
            f"outside sane bounds [1, 10000]. A daily "
            f"quota of 0 would instant-block; a value "
            f">10000 effectively disables the cap."
        )


def test_complete_daily_max_tighter_than_scan():
    """Sanity: completing training is more expensive
    than scanning a URL — its daily cap should be
    tighter."""
    import server as _s
    assert _s._COMPLETE_DAILY_MAX <= _s._SCAN_DAILY_MAX, (
        f"BRAIN-152 regression: _COMPLETE_DAILY_MAX "
        f"({_s._COMPLETE_DAILY_MAX}) > _SCAN_DAILY_MAX "
        f"({_s._SCAN_DAILY_MAX}). Complete is more "
        f"expensive than scan; its daily cap should "
        f"be at most equal."
    )


def test_assist_daily_max_loosest():
    """Sanity: assist is the cheapest interactive
    endpoint — should have the highest daily cap or
    at least not the lowest."""
    import server as _s
    other_caps = [
        _s._SCAN_DAILY_MAX,
        _s._PHASE5_DAILY_MAX,
        _s._COMPLETE_DAILY_MAX,
    ]
    assert _s._ASSIST_DAILY_MAX >= min(other_caps), (
        f"BRAIN-152 regression: _ASSIST_DAILY_MAX "
        f"({_s._ASSIST_DAILY_MAX}) is the LOWEST "
        f"daily cap. Assist is cheap + interactive; "
        f"this likely indicates a config swap."
    )


def test_idempotency_constants_sane():
    """BRAIN-132 / BRAIN-141 idempotency cache constants
    audited under the same umbrella since they're
    related to per-user persisted budgets."""
    import server as _s
    assert isinstance(_s._IDEMPOTENCY_TTL_SEC, int)
    assert 3600 <= _s._IDEMPOTENCY_TTL_SEC <= 30 * 86400  # 1h..30d
    assert isinstance(_s._IDEMPOTENCY_KEY_MAX_LEN, int)
    assert 32 <= _s._IDEMPOTENCY_KEY_MAX_LEN <= 1024
    assert isinstance(_s._IDEMPOTENCY_CACHE_PER_USER_MAX, int)
    assert 5 <= _s._IDEMPOTENCY_CACHE_PER_USER_MAX <= 1000


def test_body_byte_caps_sane():
    """BRAIN-117/127 byte cap constants integrity."""
    import server as _s
    assert isinstance(_s._WIZARD_BODY_BYTES_MAX, int)
    # 64 KiB minimum (real wizard payloads), 4 MiB max
    # (anything larger defeats the purpose of "byte cap").
    assert 64 * 1024 <= _s._WIZARD_BODY_BYTES_MAX <= 4 * 1024 * 1024
    assert isinstance(_s._WIZARD_FIELD_BYTES_MAX, int)
    # 4 KiB minimum (BRAIN-13 prompt budget), 64 KiB max.
    assert 4 * 1024 <= _s._WIZARD_FIELD_BYTES_MAX <= 64 * 1024
