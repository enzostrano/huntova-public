"""Regression tests for BRAIN-97 (a466): quota counter writes
must not amplify the durable-write count when the endpoint is
already performing a merge_settings write of its own.

Failure mode (Per Huntova engineering review on SQLite
write-amplification):

BRAIN-93/96 enforced durable daily quotas via a dedicated
`_check_paid_endpoint_quota_async` that called
`merge_settings` for every successful request. Two of the
four paid endpoints already do their own merge_settings:

- `/api/wizard/generate-phase5` does `merge_settings` to
  persist `_phase5_questions` (BRAIN-90).
- `/api/wizard/complete` does multiple merges
  (BRAIN-88 pending-flip, BRAIN-93 final merge).

For those two, the quota helper added a SECOND merge call
per request: the user-visible work persists in one txn, then
the quota counter persists in another. SQLite serializes
writes; under concurrent users this becomes hidden tail
latency that's correct functionally but slow under load.

Standard SQLite write-budget guidance: don't add an extra
durable write to a path that's already performing one.
Inline the quota increment into the existing mutator.

`/api/wizard/scan` and `/api/wizard/assist` don't currently
do their own merges (the user's response is in-memory or
returned to the client to save), so they keep the standalone
helper — there's nothing to fold into.

Invariants:
- A read-only quota check (`_read_paid_quota_async`) exists
  and returns `(blocked, current_count)` without writing.
- An in-mutator helper (`_paid_quota_inplace`) increments
  the counter inside a caller-provided mutator dict and
  returns updated `_quotas`.
- `/api/wizard/generate-phase5` uses the read+inplace pattern
  (early 429 from read, increment folded into the
  `_persist_phase5` mutator).
- `/api/wizard/complete` uses the same pattern, folded into
  the BRAIN-88 pending-flip mutator.
- `/api/wizard/scan` and `/api/wizard/assist` continue to
  use `_check_paid_endpoint_quota_async` (single-write
  semantics — nothing to fold into).
- Behavioral: quota count still increments correctly through
  the folded path (no accidental skip).
"""
from __future__ import annotations
import inspect
import asyncio


def test_read_only_quota_helper_exists():
    """Source-level: a read-only quota check (no write) must
    exist for callers that fold the increment into a
    downstream mutator."""
    import server as _s
    helper = getattr(_s, "_read_paid_quota_async", None)
    assert helper is not None, (
        "BRAIN-97 regression: server must expose "
        "`_read_paid_quota_async(user_id, bucket_name, "
        "daily_max)` so phase-5 + complete can pre-check "
        "without an extra durable write."
    )
    assert asyncio.iscoroutinefunction(helper), (
        "BRAIN-97 regression: read-only helper must be async "
        "(uses db.get_settings)."
    )


def test_inplace_quota_helper_exists():
    """Source-level: a synchronous in-mutator helper must
    exist that takes the current quotas dict and increments
    in place."""
    import server as _s
    helper = getattr(_s, "_paid_quota_inplace", None)
    assert helper is not None and callable(helper), (
        "BRAIN-97 regression: server must expose "
        "`_paid_quota_inplace(cur_quotas, bucket_name, "
        "daily_max, today)` so callers can fold the "
        "increment into their own merge mutator."
    )
    # Sync function — runs inside a merge mutator closure.
    assert not asyncio.iscoroutinefunction(helper), (
        "BRAIN-97 regression: inplace helper must be sync; "
        "merge_settings mutators are sync closures."
    )


def test_read_only_helper_does_not_write():
    """Source-level: the read-only helper must not call
    merge_settings (it's a read-only check)."""
    import server as _s
    helper = _s._read_paid_quota_async
    src = inspect.getsource(helper)
    assert "merge_settings" not in src, (
        "BRAIN-97 regression: read-only helper must not "
        "trigger a merge_settings write — that defeats the "
        "purpose of separating check from increment."
    )
    assert "get_settings" in src, (
        "BRAIN-97 regression: read-only helper must use "
        "db.get_settings to read the quota state."
    )


def test_phase5_endpoint_uses_read_then_fold_pattern():
    """Source-level: phase-5 endpoint must use the read-only
    helper for the pre-check AND fold the increment into the
    existing _persist_phase5 mutator."""
    from server import api_wizard_generate_phase5
    src = inspect.getsource(api_wizard_generate_phase5)
    # Must call the read-only helper.
    assert "_read_paid_quota_async" in src, (
        "BRAIN-97 regression: phase-5 must use the read-only "
        "helper for pre-check (no extra DB write on success "
        "since the increment folds into _persist_phase5)."
    )
    # Must call the inplace helper inside a mutator.
    assert "_paid_quota_inplace" in src, (
        "BRAIN-97 regression: phase-5 must fold the quota "
        "increment into its existing merge mutator via "
        "_paid_quota_inplace."
    )


def test_complete_endpoint_uses_read_then_fold_pattern():
    """Source-level: complete endpoint must use the same
    read-then-fold pattern, folded into the BRAIN-88
    pending-flip mutator."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    assert "_read_paid_quota_async" in src, (
        "BRAIN-97 regression: complete must use the read-only "
        "helper for pre-check."
    )
    assert "_paid_quota_inplace" in src, (
        "BRAIN-97 regression: complete must fold the quota "
        "increment into the existing BRAIN-88 pending-flip "
        "mutator."
    )


def test_scan_and_assist_still_use_standalone_helper():
    """Source-level: scan + assist don't have a fold target,
    so they continue to use _check_paid_endpoint_quota_async
    (or scan's specialized variant)."""
    from server import api_wizard_scan, api_wizard_assist
    scan_src = inspect.getsource(api_wizard_scan)
    assist_src = inspect.getsource(api_wizard_assist)
    # Scan uses the dedicated `_check_scan_daily_quota_async`.
    # Assist uses the generic `_check_paid_endpoint_quota_async`.
    scan_ok = (
        "_check_scan_daily_quota_async" in scan_src
        or "_check_paid_endpoint_quota_async" in scan_src
    )
    assert scan_ok, (
        "BRAIN-97 regression: scan still needs a quota check; "
        "the standalone helper is correct here (no fold target)."
    )
    assert "_check_paid_endpoint_quota_async" in assist_src, (
        "BRAIN-97 regression: assist still uses the standalone "
        "helper (no fold target — assist doesn't do its own "
        "merge_settings write)."
    )


def test_inplace_helper_increments_correctly_on_first_call(local_env):
    """Behavioral: the inplace helper must increment from 0
    to 1 on a fresh (date, count) state, leave it at 1, and
    not block."""
    async def _run():
        import server as _s
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        quotas, (blocked, count) = _s._paid_quota_inplace(
            {}, "wizard_phase5", 50, today
        )
        assert blocked is False
        assert count == 1
        assert quotas["wizard_phase5"]["count"] == 1
        assert quotas["wizard_phase5"]["date"] == today
    asyncio.run(_run())


def test_inplace_helper_blocks_at_or_over_cap(local_env):
    """Behavioral: when count == cap, helper blocks AND does
    not increment further."""
    async def _run():
        import server as _s
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        seeded = {"wizard_phase5": {"date": today, "count": 50}}
        quotas, (blocked, count) = _s._paid_quota_inplace(
            seeded, "wizard_phase5", 50, today
        )
        assert blocked is True
        assert count == 50
        assert quotas["wizard_phase5"]["count"] == 50
    asyncio.run(_run())


def test_inplace_helper_resets_on_utc_date_rollover(local_env):
    """Behavioral: stale date in the dict triggers a fresh
    {date, count: 0} reset before incrementing."""
    async def _run():
        import server as _s
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # Seed with yesterday's exhausted counter.
        seeded = {"wizard_phase5": {"date": "2020-01-01", "count": 50}}
        quotas, (blocked, count) = _s._paid_quota_inplace(
            seeded, "wizard_phase5", 50, today
        )
        assert blocked is False, (
            "BRAIN-97 regression: stale date must trigger reset; "
            "yesterday's exhausted counter must not block today."
        )
        assert count == 1
        assert quotas["wizard_phase5"]["date"] == today
    asyncio.run(_run())
