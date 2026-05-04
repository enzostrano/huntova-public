"""Regression tests for BRAIN-105 (a474): the wizard audit
counter must distinguish "retrain attempted" from "retrain
executed". The BRAIN-85/101 short-circuit currently leaves
`_train_count` unchanged, so the operator-facing audit
collapses two distinct outcomes into the same stored state.

Failure mode (Per Huntova engineering review on
audit-trail accuracy):

`_train_count` was bumped only inside the full-pipeline merge
mutator. The BRAIN-85 idempotency cache hit returns
`{ok: true, reused: true}` early WITHOUT going through that
mutator. So a user who clicks "Re-train" with unchanged
inputs five times in a row produces:

- Stored `_train_count`: still N (the original execution).
- Stored `_train_attempts`: nothing recorded.
- Operator dashboard: "1 retrain in this period."
- Reality: 5 attempts, 1 execution + 4 short-circuits.

Audit collapses "user actively asked for retrain but it
was skipped as idempotent" with "user did nothing." That's
exactly the failure mode audit data is supposed to prevent.

Standard audit-trail invariant: accepted intent must be
recorded even when execution is skipped.

Invariants:
- New `_train_attempts` counter on the wizard blob.
- Bumped on EVERY accepted `/api/wizard/complete` invocation
  — including the BRAIN-85/101 short-circuit path.
- Existing `_train_count` semantics preserved: only bumped
  on full-pipeline completes.
- Status endpoint exposes BOTH `train_attempts` and
  `train_count` so the UI/operator can see the gap.
- The short-circuit path uses a small dedicated
  `merge_settings` call (one extra DB write per duplicate
  submit) — accepted cost for audit truth.
"""
from __future__ import annotations
import inspect


def test_train_attempts_bumped_on_short_circuit():
    """Source-level: BRAIN-85 short-circuit branch in
    api_wizard_complete must bump `_train_attempts`."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    # Find the cache-hit return.
    fire_idx = src.find('"reused": True')
    if fire_idx == -1:
        fire_idx = src.find("'reused': True")
    assert fire_idx != -1
    # Look in the ~1500 chars before the return for the bump.
    block = src[max(0, fire_idx - 2000):fire_idx]
    assert "_train_attempts" in block, (
        "BRAIN-105 regression: short-circuit path must bump "
        "`_train_attempts` so the audit captures retrain "
        "intent even when execution is skipped."
    )


def test_train_attempts_bumped_in_full_pipeline_too():
    """Source-level: the existing full-pipeline merge mutator
    must ALSO bump `_train_attempts` — every accepted
    invocation increments attempts, regardless of which
    path runs."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    # Find the existing _train_count bump line.
    tc_idx = src.find('w["_train_count"] = ')
    assert tc_idx != -1
    # _train_attempts should be bumped right next to it.
    block = src[max(0, tc_idx - 200):tc_idx + 500]
    assert "_train_attempts" in block, (
        "BRAIN-105 regression: full-pipeline path must also "
        "bump `_train_attempts` alongside `_train_count`. "
        "The attempts counter is monotonic across both paths."
    )


def test_status_endpoint_exposes_both_counters():
    """Source-level: /api/wizard/status must expose both
    `train_count` (executions) and `train_attempts`
    (attempts incl. short-circuit) so the UI/operator can
    see the gap."""
    from server import api_wizard_status
    src = inspect.getsource(api_wizard_status)
    has_attempts = (
        "train_attempts" in src or "_train_attempts" in src
    )
    assert has_attempts, (
        "BRAIN-105 regression: status endpoint must expose "
        "`train_attempts` so the dashboard shows attempts "
        "vs executions."
    )


def test_short_circuit_uses_atomic_merge_for_attempts():
    """Source-level: the short-circuit attempts-bump must
    use db.merge_settings (atomic), NOT a get-modify-save
    pattern that would race with the full-pipeline path."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    fire_idx = src.find('"reused": True')
    if fire_idx == -1:
        fire_idx = src.find("'reused': True")
    block = src[max(0, fire_idx - 2000):fire_idx]
    assert "merge_settings" in block, (
        "BRAIN-105 regression: short-circuit attempts bump "
        "must go through atomic merge_settings — concurrent "
        "short-circuits + full-pipeline writes can race "
        "otherwise."
    )


def test_short_circuit_attempts_bump_failure_is_non_fatal():
    """Source-level: a DB transient on the attempts-bump
    must not block the short-circuit response. Audit
    accuracy is best-effort; user functionality is not."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    # Look for the try/except pattern around the attempts merge.
    fire_idx = src.find('"reused": True')
    if fire_idx == -1:
        fire_idx = src.find("'reused': True")
    block = src[max(0, fire_idx - 2000):fire_idx]
    has_defensive = "try:" in block and "except" in block
    assert has_defensive, (
        "BRAIN-105 regression: short-circuit attempts-bump "
        "must be try/except'd. A DB blip mustn't 500 the "
        "user when the only failure is audit precision."
    )
