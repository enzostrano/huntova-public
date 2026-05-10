"""Regression tests for BRAIN-93 (a462): the wizard scan daily
quota (BRAIN-92) must persist to durable storage so it survives
process restarts, deploys, and worker crashes.

Failure mode (Per Huntova engineering review on quota durability):

BRAIN-92 stored the per-user daily counter in
`_scan_daily_state: dict[(user_id, date), int]` — process-local
in-memory only. Failure shape:

1. User hits 50 scans → daily quota exhausted. Server returns
   429 `daily_quota_exceeded`.
2. Operator deploys a new release (or the worker restarts on
   crash, or scaling spawns a new process).
3. New process boots with empty `_scan_daily_state`.
4. User's NEXT scan request returns 200 — the counter
   restarted at 0.
5. User can drain another 50 scans before hitting the cap
   again. Per UTC day, the effective cap becomes
   `50 × n_restarts`, not `50`.

Standard guidance for spend-control quotas: counters must be
durable and shared. BRAIN-92's docstring noted this gap
("If multi-process becomes the deployment shape later, this
needs Redis or a DB row").

Invariants:
- An async `_check_scan_daily_quota_async(user_id)` helper
  exists. It uses `db.merge_settings` to atomically check +
  increment the counter under `_quotas.wizard_scan` in the
  user's settings JSON.
- Counter survives process restart: reinitializing
  `_scan_daily_state` (in-memory cache) does NOT reset the
  effective limit; the next scan still reads the persisted
  count from the DB.
- The wizard reset (BRAIN-80) does NOT refund quota — the
  counter lives at the settings root, NOT inside `wizard`,
  so the BRAIN-80 full-wipe doesn't touch it.
- Date keying uses `datetime.now(timezone.utc)` (not the
  deprecated `datetime.utcnow()`).
- Endpoint calls the async variant before any crawl/AI work.
"""
from __future__ import annotations
import inspect
import asyncio


def test_async_quota_helper_exists():
    """Source-level: a `_check_scan_daily_quota_async` (or
    similarly-named async) helper must exist on `server`."""
    import server as _s
    helper = getattr(_s, "_check_scan_daily_quota_async", None)
    assert helper is not None, (
        "BRAIN-93 regression: server must expose "
        "`_check_scan_daily_quota_async(user_id)` as the "
        "durable variant of the BRAIN-92 helper."
    )
    # Must be a coroutine.
    assert asyncio.iscoroutinefunction(helper), (
        "BRAIN-93 regression: durable quota helper must be "
        "async (uses db.merge_settings under the hood)."
    )


def test_async_quota_uses_merge_settings():
    """Source-level: the async helper must use
    `db.merge_settings` so the check + increment is atomic
    even under concurrent scan requests for the same user."""
    import server as _s
    helper = _s._check_scan_daily_quota_async
    src = inspect.getsource(helper)
    assert "merge_settings" in src, (
        "BRAIN-93 regression: durable quota helper must call "
        "db.merge_settings for atomic check+increment."
    )


def test_async_quota_uses_timezone_aware_utc():
    """Source-level: the helper must use the timezone-aware
    `datetime.now(timezone.utc)` form. `datetime.utcnow()` is
    deprecated in Python 3.12+."""
    import server as _s
    helper = _s._check_scan_daily_quota_async
    src = inspect.getsource(helper)
    # Either explicit `now(timezone.utc)` or `now(tz=...)` is fine.
    has_aware = (
        "now(timezone.utc)" in src
        or "now(tz=timezone.utc)" in src
        or "datetime.now(UTC" in src
    )
    assert has_aware, (
        "BRAIN-93 regression: helper must use "
        "`datetime.now(timezone.utc)`. `utcnow()` is "
        "deprecated and emits a warning in 3.12+."
    )
    assert "utcnow" not in src, (
        "BRAIN-93 regression: helper must not use the "
        "deprecated `datetime.utcnow()`."
    )


def test_quota_lives_at_settings_root_not_inside_wizard():
    """Source-level: the counter must live OUTSIDE the wizard
    sub-object. The BRAIN-80 wizard reset full-wipes
    `cur["wizard"]`; if the quota lived inside, reset would
    refund it — defeating the cap."""
    import server as _s
    src = inspect.getsource(_s._check_scan_daily_quota_async)
    # The mutator should write to a top-level key on `cur`, not
    # to `cur["wizard"]`. Match against `cur["_quotas"]` or
    # equivalent root-level placement.
    assert '"_quotas"' in src or "'_quotas'" in src or "_quotas" in src, (
        "BRAIN-93 regression: counter must live at settings "
        "root (e.g. `cur['_quotas']`), NOT inside `cur['wizard']`. "
        "Otherwise BRAIN-80 reset refunds the daily quota."
    )
    # Defensively check the mutator does NOT write under wizard.
    # We grep for the assignment pattern.
    assert 'w["_quotas"]' not in src and 'w[\'_quotas\']' not in src, (
        "BRAIN-93 regression: quota must NOT be assigned under "
        "the `wizard` sub-object (would be wiped by reset)."
    )


def test_scan_endpoint_uses_async_quota_helper():
    """Source-level: api_wizard_scan must call the async
    helper. The legacy in-memory `_check_scan_daily_quota`
    can stay as a fallback or be deprecated, but the async
    variant must be the load-bearing path."""
    from server import api_wizard_scan
    src = inspect.getsource(api_wizard_scan)
    assert "_check_scan_daily_quota_async" in src, (
        "BRAIN-93 regression: scan endpoint must call the "
        "durable async quota helper. The in-memory variant "
        "doesn't survive restart."
    )


def test_async_quota_blocks_when_count_at_or_over_limit():
    """Source-level: the mutator must block when count >=
    _SCAN_DAILY_MAX (NOT >). Off-by-one would let the user
    sneak one extra scan past the cap."""
    import server as _s
    src = inspect.getsource(_s._check_scan_daily_quota_async)
    # The blocking comparison should be >= against _SCAN_DAILY_MAX.
    assert ">= _SCAN_DAILY_MAX" in src or ">= _s._SCAN_DAILY_MAX" in src, (
        "BRAIN-93 regression: blocking comparison must be `>= "
        "_SCAN_DAILY_MAX` (not `>`). Off-by-one would allow 51 "
        "scans on the 50/day cap."
    )


def test_async_quota_resets_on_new_utc_date():
    """Source-level: when the persisted date differs from
    today's UTC date, the mutator must reset the counter to 0
    before incrementing — not carry yesterday's count
    forward."""
    import server as _s
    src = inspect.getsource(_s._check_scan_daily_quota_async)
    # Look for the date comparison + reset pattern.
    assert (
        '!=' in src and 'date' in src.lower()
        and ('"count": 0' in src or "'count': 0" in src or "count = 0" in src)
    ), (
        "BRAIN-93 regression: mutator must compare stored date "
        "vs today's UTC date and reset count to 0 on mismatch. "
        "Otherwise yesterday's count carries forward and "
        "blocks the user on day 2."
    )
