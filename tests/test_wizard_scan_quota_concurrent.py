"""Regression tests for BRAIN-94 (a463): the BRAIN-93 durable
daily scan quota must remain race-safe under concurrent
requests for the same user.

Failure mode (Per Huntova engineering review on quota
oversubscription):

The classic check-then-increment race: two concurrent requests
near the limit both read `count = 49`, both pass the
`< _SCAN_DAILY_MAX` check, both increment, final count = 51 —
one scan beyond the cap.

BRAIN-93 wraps the check + increment inside `db.merge_settings`,
which gives atomicity per BRAIN-6 (a347): SQLite uses driver
RLock + `BEGIN IMMEDIATE`; Postgres uses `SELECT … FOR UPDATE`.
Both serialize concurrent writers for the same user_id.

This release adds a behavioral regression test that verifies the
serialization actually holds. If the underlying merge_settings
ever loses its lock (refactor regression, _xlate bug, etc.), the
quota would silently break and this test catches it.

Invariants:
- Seeding `_quotas.wizard_scan.count = _SCAN_DAILY_MAX - 1`,
  firing N concurrent quota checks for the same user, must
  yield EXACTLY 1 success and N-1 blocks.
- Final persisted count = `_SCAN_DAILY_MAX` (never goes over).
"""
from __future__ import annotations
import asyncio


def test_concurrent_quota_checks_never_oversubscribe(local_env):
    """Behavioral: 5 concurrent calls at the limit-1 boundary
    must result in exactly 1 success and 4 blocks. The persisted
    count must end at exactly _SCAN_DAILY_MAX, never above."""
    async def _run():
        from db import init_db, create_user, merge_settings, get_settings
        from auth import hash_password
        from server import _check_scan_daily_quota_async, _SCAN_DAILY_MAX
        from datetime import datetime, timezone
        await init_db()
        uid = await create_user(
            "race-quota@example.com", hash_password("p"), "RQ"
        )
        # Seed quota at limit - 1 so exactly one of the
        # concurrent calls should succeed.
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        seeded_count = _SCAN_DAILY_MAX - 1

        def _seed(cur):
            cur = dict(cur or {})
            cur["_quotas"] = {
                "wizard_scan": {"date": today, "count": seeded_count}
            }
            return cur

        await merge_settings(uid, _seed)

        # Fire 5 quota checks concurrently. asyncio.gather
        # interleaves them at the merge_settings boundary.
        results = await asyncio.gather(*[
            _check_scan_daily_quota_async(uid) for _ in range(5)
        ])

        blocks = [r for r in results if r is True]
        successes = [r for r in results if r is False]
        assert len(successes) == 1, (
            f"BRAIN-94 regression: expected exactly 1 success at "
            f"limit-1 with 5 concurrent calls; got "
            f"{len(successes)} successes, {len(blocks)} blocks. "
            f"Quota oversubscribed under concurrency."
        )
        assert len(blocks) == 4, (
            f"BRAIN-94 sanity: expected 4 blocks; got "
            f"{len(blocks)}. results={results!r}"
        )

        # Verify the persisted count never exceeded the cap.
        s = await get_settings(uid)
        scan_q = ((s.get("_quotas") or {}).get("wizard_scan") or {})
        final_count = int(scan_q.get("count", -1))
        assert final_count == _SCAN_DAILY_MAX, (
            f"BRAIN-94 regression: persisted count is "
            f"{final_count}, expected {_SCAN_DAILY_MAX}. "
            f"Quota over/under-subscribed under concurrency."
        )

    asyncio.run(_run())


def test_quota_check_serialised_under_higher_contention(local_env):
    """Behavioral: 20 concurrent calls starting from a fresh
    user (count=0) must yield exactly _SCAN_DAILY_MAX successes
    and (20 - _SCAN_DAILY_MAX) blocks if 20 > _SCAN_DAILY_MAX,
    otherwise 20 successes. Verifies the serialization still
    holds as concurrency scales — not just at the boundary."""
    async def _run():
        from db import init_db, create_user, get_settings
        from auth import hash_password
        from server import _check_scan_daily_quota_async, _SCAN_DAILY_MAX
        await init_db()
        uid = await create_user(
            "race-quota-burst@example.com", hash_password("p"), "RQB"
        )
        N = 20
        results = await asyncio.gather(*[
            _check_scan_daily_quota_async(uid) for _ in range(N)
        ])

        blocks = sum(1 for r in results if r is True)
        successes = sum(1 for r in results if r is False)
        expected_successes = min(N, _SCAN_DAILY_MAX)
        expected_blocks = N - expected_successes
        assert successes == expected_successes, (
            f"BRAIN-94 regression: under N={N} concurrent calls, "
            f"expected {expected_successes} successes (capped at "
            f"_SCAN_DAILY_MAX={_SCAN_DAILY_MAX}); got "
            f"{successes}. Quota oversubscribed."
        )
        assert blocks == expected_blocks, (
            f"BRAIN-94 sanity: expected {expected_blocks} blocks; "
            f"got {blocks}."
        )

        # Verify final count.
        s = await get_settings(uid)
        scan_q = ((s.get("_quotas") or {}).get("wizard_scan") or {})
        final_count = int(scan_q.get("count", -1))
        assert final_count == expected_successes, (
            f"BRAIN-94 regression: persisted count {final_count} "
            f"!= expected_successes {expected_successes}."
        )

    asyncio.run(_run())
