"""Regression tests for BRAIN-106 (a475): the BRAIN-85
idempotency short-circuit must remain a fast path. The
BRAIN-105 attempts-bump introduced a synchronous DB write
on every duplicate submit, eroding the latency advantage.

Failure mode (Per Huntova engineering review on
short-circuit fast-path preservation):

The whole point of BRAIN-85's idempotency cache is to make
duplicate submits cheap — both in BYOK spend (the brain/
dossier/DNA pipeline doesn't run) AND in DB load (no
multi-merge churn on the user_settings row). BRAIN-105
restored audit visibility by adding a synchronous
`merge_settings` to bump `_train_attempts` on every
short-circuit hit.

Net: a duplicate complete now does:

- 1 read (`get_settings` for cache check)
- 1 synchronous merge_settings write (attempts bump)
- Return

That's still cheaper than the full path (2 merges + AI calls
+ ~5-30s compute), but for the exact scenario idempotency
is meant to absorb (rapid retries, double-clicks, flaky
clients), the fast path is no longer truly fast under
repetition. Each duplicate adds DB pressure roughly equal
to a save-progress write — the wizard's hottest write path.

Standard idempotency-pattern guidance: the cached hit must
perform strictly less durable work than the cache miss.
Audit signals that need to be present can be either folded
into an existing write, sampled, or moved off the synchronous
path.

Invariants:
- The short-circuit's `_train_attempts` bump uses
  `_spawn_bg` (fire-and-forget), not `await
  db.merge_settings`. The user's response returns
  immediately; audit lands eventually.
- The full-pipeline path's bump stays inline (it's already
  inside an existing merge, no extra cost).
- The attempts-bump bg task still uses atomic
  `db.merge_settings` so concurrent bumps from rapid
  retries don't race.
"""
from __future__ import annotations
import inspect


def test_short_circuit_attempts_bump_uses_spawn_bg():
    """Source-level: the short-circuit attempts-bump must
    fire via `_spawn_bg`, not `await db.merge_settings`."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    fire_idx = src.find('"reused": True')
    if fire_idx == -1:
        fire_idx = src.find("'reused': True")
    assert fire_idx != -1
    block = src[max(0, fire_idx - 2000):fire_idx]
    # The BRAIN-105 bump function name is
    # _bump_attempts_short_circuit; it must be invoked via
    # _spawn_bg in the short-circuit branch.
    assert "_spawn_bg(" in block, (
        "BRAIN-106 regression: short-circuit attempts-bump "
        "must use `_spawn_bg(...)` (fire-and-forget) instead "
        "of `await db.merge_settings(...)`. The cached hit "
        "must remain materially cheaper than the cache miss."
    )


def test_short_circuit_dispatches_bump_via_spawn_bg_not_await():
    """Source-level: the short-circuit branch must use
    `_spawn_bg(...)` to dispatch the attempts-bump. The
    `await db.merge_settings(...)` may appear INSIDE the bg
    coroutine (atomicity for concurrent bumps) but `_spawn_bg`
    must precede the `return` so the user response doesn't
    block on the bump."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    fire_idx = src.find('"reused": True')
    if fire_idx == -1:
        fire_idx = src.find("'reused': True")
    assert fire_idx != -1
    block = src[max(0, fire_idx - 2000):fire_idx]
    # _spawn_bg must come BEFORE the cache-hit return.
    spawn_idx = block.rfind("_spawn_bg(")
    assert spawn_idx != -1, (
        "BRAIN-106 regression: short-circuit branch must "
        "dispatch the attempts-bump via `_spawn_bg(...)` "
        "before returning."
    )
    # The await INSIDE the bg coroutine is fine — it runs
    # off the synchronous request path. Verify the
    # `await db.merge_settings` only appears AFTER an
    # `async def` defining the bg coroutine (i.e. it's
    # nested inside a closure, not at the request path's
    # top level).
    async_def_idx = block.rfind("async def _bg_")
    if async_def_idx == -1:
        async_def_idx = block.rfind("async def ")
    await_idx = block.rfind("await db.merge_settings(")
    if await_idx != -1:
        assert async_def_idx != -1 and async_def_idx < await_idx, (
            "BRAIN-106 regression: `await db.merge_settings` "
            "appears in the short-circuit branch outside any "
            "`async def` bg-coroutine definition. The bump "
            "must run off the synchronous path."
        )


def test_full_pipeline_attempts_bump_stays_inline():
    """The full-pipeline bump shares the existing brain/
    dossier merge mutator — no extra cost. Don't regress
    that into a separate spawn_bg call."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    # Find the existing _train_count bump line.
    tc_idx = src.find('w["_train_count"] = ')
    assert tc_idx != -1
    # _train_attempts should be bumped right next to it.
    block = src[max(0, tc_idx - 200):tc_idx + 500]
    assert '_train_attempts' in block, (
        "BRAIN-106 sanity: full-pipeline path should still "
        "bump _train_attempts inline (BRAIN-105). Fast-path "
        "fix shouldn't accidentally move this off the "
        "existing merge."
    )


def test_bg_task_still_uses_atomic_merge():
    """Source-level: the spawned background task must STILL
    use `db.merge_settings` so concurrent rapid-retry bumps
    don't race. Fire-and-forget doesn't mean fire-and-forget-
    atomicity."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    fire_idx = src.find('"reused": True')
    if fire_idx == -1:
        fire_idx = src.find("'reused': True")
    block = src[max(0, fire_idx - 2000):fire_idx]
    assert "merge_settings" in block, (
        "BRAIN-106 regression: the bg attempts-bump task "
        "must still use `db.merge_settings` for atomicity. "
        "Two rapid retries would otherwise both read "
        "attempts=N, both write N+1, and the counter "
        "under-counts."
    )
