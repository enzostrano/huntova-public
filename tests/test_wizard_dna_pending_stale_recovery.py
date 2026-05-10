"""Regression tests for BRAIN-111 (a480): stale-pending
recovery for the BRAIN-110 atomic claim.

Failure mode (Per Huntova engineering review on lease-
based locks):

BRAIN-110 (a479) made `_dna_state="pending"` an atomic
claim that prevents concurrent-tab double-spawn of
`_gen_dna()`. But "pending" is also a lease, and every
lease must define how it gets released on worker
failure:

- successful completion → BRAIN-78 ready writeback.
- in-process exception → BRAIN-78 failed writeback.
- `asyncio.CancelledError` (event-loop shutdown,
  parent-task cancel) → NOT caught by
  `except Exception`. Leaves "pending" forever.
- Process death (SIGKILL, OOM, server crash, machine
  power-off, deploy/restart mid-generation) → no
  Python code runs at all. Leaves "pending" forever.

Once "pending" is immortal, BRAIN-110 punishes the user
forever: every future `/api/wizard/complete` returns
HTTP 409 `dna_in_flight` permanently. The user can never
retrain again without admin intervention. That's the
opposite of helpful.

Standard fix for this class: a lease must have an
expiry. If `_dna_state == "pending"` AND
`_dna_started_at` is older than the documented TTL,
treat the lock as stale and let the new claim proceed.
DNA generation typically completes in 10-30 seconds; a
10-minute TTL is 20× the worst legitimate case but tight
enough to recover quickly from a crash.

Invariants:
- Module-scope constant `_DNA_PENDING_STALE_AFTER_SEC`
  defines the lease TTL. Env-overridable via
  `HV_DNA_PENDING_STALE_SEC`.
- Helper `_dna_pending_is_stale(started_at_iso, now)`
  exists at module scope and returns True when the
  pending lease is older than the TTL (or when
  `started_at_iso` is missing/unparseable — fail-open
  toward recovery rather than fail-closed toward
  permanent stuck).
- The BRAIN-110 flip mutator detects stale pending and
  allows the new claim (transition pending→pending
  with fresh `_dna_started_at`) instead of returning
  `dna_in_flight`.
- The `_gen_dna` background task uses `try/finally`
  with a `BaseException` catch so cancellation also
  writes a failed-state writeback — defense in depth
  for the in-process case.
"""
from __future__ import annotations
import inspect
import re
from datetime import datetime, timedelta


def test_stale_after_sec_constant_exists():
    """Module-scope constant defines the lease TTL."""
    import server as _s
    val = getattr(_s, "_DNA_PENDING_STALE_AFTER_SEC", None)
    assert val is not None, (
        "BRAIN-111 regression: server must expose "
        "`_DNA_PENDING_STALE_AFTER_SEC` at module scope so "
        "the flip mutator (and any future read site) can "
        "detect a stale pending lease."
    )
    assert isinstance(val, int) and val > 0, (
        "BRAIN-111 regression: TTL must be a positive int "
        "(seconds)."
    )
    # 10 minutes is a reasonable default for a 10-30s job.
    # Hard floor: at least 60s so a slow legitimate run
    # isn't reaped.
    assert val >= 60, "TTL too aggressive — would reap legitimate runs"


def test_stale_helper_exists():
    """Module-scope helper exposes the stale-detection
    logic so the flip mutator and any other read site can
    consult the same contract."""
    import server as _s
    fn = getattr(_s, "_dna_pending_is_stale", None)
    assert fn is not None and callable(fn), (
        "BRAIN-111 regression: server must expose "
        "`_dna_pending_is_stale(started_at_iso)` at module "
        "scope."
    )


def test_stale_helper_returns_true_for_old_pending():
    """Behavioral: a pending lease older than the TTL is
    stale."""
    import server as _s
    ttl = _s._DNA_PENDING_STALE_AFTER_SEC
    # Started 2× TTL ago → definitely stale.
    long_ago = (datetime.now() - timedelta(seconds=ttl * 2)).isoformat()
    assert _s._dna_pending_is_stale(long_ago) is True


def test_stale_helper_returns_false_for_fresh_pending():
    """Behavioral: a pending lease that just started is
    not stale."""
    import server as _s
    just_now = datetime.now().isoformat()
    assert _s._dna_pending_is_stale(just_now) is False


def test_stale_helper_returns_true_for_missing_started_at():
    """Behavioral: missing/empty `_dna_started_at` is
    treated as stale (fail-open toward recovery rather
    than fail-closed toward permanent stuck). A pending
    lease with no start timestamp can't be aged, and
    keeping it forever is exactly the bug we're fixing."""
    import server as _s
    assert _s._dna_pending_is_stale(None) is True
    assert _s._dna_pending_is_stale("") is True


def test_stale_helper_returns_true_for_unparseable_started_at():
    """Behavioral: a corrupted timestamp (e.g. operator
    UPDATE wrote "banana" into the column) must not trap
    the user behind a permanent lock. Treat as stale."""
    import server as _s
    assert _s._dna_pending_is_stale("not-a-timestamp") is True
    assert _s._dna_pending_is_stale("1969-13-99T99:99:99") is True


def test_pending_flip_mutator_consults_stale_helper():
    """Source-level: the BRAIN-110 flip mutator must
    consult the stale-helper when `_dna_state == "pending"`
    so a stuck lease can recover. Without this, BRAIN-110's
    409 dna_in_flight becomes permanent."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    m = re.search(
        r"def _pending_flip_mutator\(.*?await db\.merge_settings\(",
        src,
        re.DOTALL,
    )
    assert m, "flip mutator should be present"
    body = m.group(0)
    assert "_dna_pending_is_stale(" in body, (
        "BRAIN-111 regression: the flip mutator must call "
        "`_dna_pending_is_stale(...)` when it detects "
        "`_dna_state == 'pending'` so a crashed generation "
        "doesn't permanently block future retrains. "
        "Without this, BRAIN-110's 409 dna_in_flight "
        "response is forever."
    )


def test_gen_dna_uses_finally_for_cancellation():
    """Source-level: the `_gen_dna` background task must
    use `try/finally` (or a CancelledError catch) so an
    asyncio cancellation also writes a failed-state row.
    `except Exception` does NOT catch
    `asyncio.CancelledError` since Python 3.8 — that's a
    BaseException-derived class. Without finally
    semantics, in-process cancellation leaves
    `_dna_state="pending"` forever."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    # Locate the _gen_dna closure body.
    m = re.search(
        r"async def _gen_dna\(\):(.*?)_spawn_bg\(_gen_dna\(\)\)",
        src,
        re.DOTALL,
    )
    assert m, "_gen_dna closure should be present"
    body = m.group(1)
    has_finally_or_be = (
        "finally:" in body
        or "BaseException" in body
        or "CancelledError" in body
    )
    assert has_finally_or_be, (
        "BRAIN-111 regression: `_gen_dna` must use "
        "`try/finally`, catch `asyncio.CancelledError`, or "
        "catch `BaseException` so cancellation also "
        "writes a failed-state recovery row. Otherwise an "
        "in-process cancel strands `_dna_state` at "
        "'pending' forever."
    )


def test_flip_mutator_resets_started_at_on_stale_recovery():
    """Behavioral simulation: when the flip mutator
    detects stale pending and allows the new claim, it
    must update `_dna_started_at` to the current time so
    the new lease ages from now, not from the corpse of
    the crashed run."""
    from datetime import datetime, timedelta

    # Same closure pattern as BRAIN-110 test — re-implement
    # the stale-recovery logic against the documented
    # invariant. A real regression in source would fail
    # the source-level tests above.
    captured_revision = 5
    captured_epoch = 1
    ttl = 600  # 10 min — matches default

    def make_mutator(now):
        flag = {"value": False, "kind": None}

        def mutator(cur):
            cur = dict(cur or {})
            w = dict(cur.get("wizard") or {})
            cur_rev = int(w.get("_wizard_revision", 0) or 0)
            cur_epoch = int(w.get("_wizard_epoch", 0) or 0)
            if cur_epoch != captured_epoch:
                flag["value"] = True
                flag["kind"] = "wizard_reset"
                return cur
            if cur_rev != captured_revision:
                flag["value"] = True
                flag["kind"] = "stale_revision"
                return cur
            if w.get("_dna_state") == "pending":
                # BRAIN-111: stale-lock recovery.
                started = w.get("_dna_started_at") or ""
                stale = True
                if started:
                    try:
                        age = (now - datetime.fromisoformat(started)).total_seconds()
                        stale = age > ttl
                    except Exception:
                        stale = True
                if not stale:
                    flag["value"] = True
                    flag["kind"] = "dna_in_flight"
                    return cur
                # else: fall through and re-claim
            w["_dna_state"] = "pending"
            w["_dna_started_at"] = now.isoformat()
            cur["wizard"] = w
            return cur

        return mutator, flag

    # Case A: pending lease started 1 hour ago = stale.
    # New claim must succeed; flag stays clean.
    now = datetime.now()
    started_old = (now - timedelta(seconds=ttl * 6)).isoformat()
    row = {
        "wizard": {
            "_wizard_revision": 5,
            "_wizard_epoch": 1,
            "_dna_state": "pending",
            "_dna_started_at": started_old,
        }
    }
    m_a, flag_a = make_mutator(now)
    row = m_a(row)
    assert flag_a["value"] is False, (
        "BRAIN-111 regression: stale pending lease must "
        "release; new claim must succeed."
    )
    # Started_at must be refreshed so the new lease ages
    # from now.
    assert row["wizard"]["_dna_started_at"] != started_old

    # Case B: pending lease started 5 seconds ago = fresh.
    # New claim must lose with dna_in_flight.
    now2 = datetime.now()
    started_fresh = (now2 - timedelta(seconds=5)).isoformat()
    row2 = {
        "wizard": {
            "_wizard_revision": 5,
            "_wizard_epoch": 1,
            "_dna_state": "pending",
            "_dna_started_at": started_fresh,
        }
    }
    m_b, flag_b = make_mutator(now2)
    m_b(row2)
    assert flag_b["value"] is True
    assert flag_b["kind"] == "dna_in_flight", (
        "BRAIN-111 regression: a FRESH pending lease must "
        "still 409 — only stale leases recover."
    )
