"""Regression tests for BRAIN-88 (a457): Re-train must atomically
flip `_dna_state` from "ready" to "pending" on submit acceptance,
BEFORE the brain+dossier compute window opens.

Failure mode (Per Huntova engineering review on durable-workflow
state-truth):

After BRAIN-78 (durable DNA state) the merge mutator inside
`api_wizard_complete` sets `_dna_state="pending"` — but only at
the END of the request, after the brain+dossier compute (BRAIN-72
watchdog up to 45s). Sequence pre-fix:

1. User has a trained wizard, `_dna_state="ready"`.
2. User re-enters wizard, edits inputs, clicks Re-train.
3. Server reads snapshot (still "ready"), validates, BRAIN-85
   fingerprint check (mismatch → no short-circuit), starts the
   brain+dossier compute.
4. **Compute window: 5-30s, can be up to 45s.** During this
   window:
   - `/api/wizard/status` returns `dna_state: "ready"` (the
     row hasn't been touched yet).
   - `/agent/control action=start` reads `_dna_state="ready"`
     and proceeds — using the OLD DNA to run a hunt against
     the user's NEW inputs. Quality drift, no signal.
5. Compute finishes → final merge fires → state finally moves
   to "pending" briefly, then "ready" when the bg DNA job
   completes.

The product lies about training-artifact authority during
regeneration.

Standard fix: split the merge in two. An EARLY merge runs
right after the BRAIN-85 cache miss and atomically flips
`ready → pending` (clearing the prior ready timestamps). Then
the brain+dossier compute runs. Then the FINAL merge writes
the new artifacts. Status / agent-gate consumers see "pending"
immediately.

Invariants:
- A new merge call (the "pending-flip") fires AFTER the
  BRAIN-85 short-circuit check but BEFORE
  `_apply_wizard_mutations(_w_snap)` + brain compute.
- The pending-flip mutator sets `_dna_state="pending"` +
  `_dna_started_at` + clears `_dna_error` / `_dna_failed_at` /
  `_dna_completed_at`.
- The pending-flip honors the BRAIN-14 revision guard +
  BRAIN-81 epoch guard. A stale tab cannot smuggle a flip past
  those checks.
- The original final merge inside `_mutator` still writes
  brain + dossier + train_count + knowledge entry as before;
  the pending-flip is additive, not a replacement.
- BRAIN-79 agent-gate sees "pending" immediately after the
  Re-train submit — no stale "ready" window during compute.
- BRAIN-85 short-circuit still fires for identical-profile
  resubmits, BEFORE the pending-flip. Duplicate submits don't
  disturb durable state.
"""
from __future__ import annotations
import inspect


def test_complete_endpoint_runs_early_pending_flip():
    """Source-level: api_wizard_complete must invoke an
    additional merge_settings call (the pending-flip) that
    sets _dna_state="pending" before the brain+dossier work."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    # Look for a named pending-flip mutator.
    has_flip = (
        "_pending_flip_mutator" in src
        or "_dna_pending_mutator" in src
        or "_early_pending_mutator" in src
        or "_retrain_flip_mutator" in src
    )
    assert has_flip, (
        "BRAIN-88 regression: api_wizard_complete must invoke a "
        "pending-flip merge before the brain+dossier compute "
        "window. Without it, status + agent-gate see stale "
        "'ready' during the multi-second compute."
    )


def test_pending_flip_runs_before_brain_dossier_compute():
    """Source-level: the pending-flip merge must run BEFORE
    `_apply_wizard_mutations(_w_snap)` and the
    `asyncio.wait_for(asyncio.to_thread(_build_artifacts_sync,
    ...))` watchdog'd compute."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    # Find the pending-flip identifier.
    flip_idx = -1
    for needle in ("_pending_flip_mutator", "_dna_pending_mutator",
                   "_early_pending_mutator", "_retrain_flip_mutator"):
        i = src.find(needle)
        if i != -1:
            flip_idx = i if flip_idx == -1 else min(flip_idx, i)
    apply_idx = src.find("_apply_wizard_mutations(_w_snap)")
    build_idx = src.find("_build_artifacts_sync")
    assert flip_idx != -1
    assert apply_idx != -1
    assert build_idx != -1
    assert flip_idx < apply_idx, (
        "BRAIN-88 regression: pending-flip must run BEFORE "
        "_apply_wizard_mutations on the snapshot. Otherwise "
        "the snapshot/compute path runs against stale state."
    )
    assert flip_idx < build_idx, (
        "BRAIN-88 regression: pending-flip must run BEFORE the "
        "brain+dossier watchdog'd compute, otherwise consumers "
        "see stale 'ready' for the entire compute window."
    )


def test_pending_flip_runs_AFTER_idempotent_short_circuit():
    """The BRAIN-85 cache must short-circuit BEFORE the
    pending-flip. Otherwise an identical-profile duplicate
    submit would gratuitously move durable state to pending +
    back to ready (churning the row + bouncing the UI badge)."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    flip_idx = -1
    for needle in ("_pending_flip_mutator", "_dna_pending_mutator",
                   "_early_pending_mutator", "_retrain_flip_mutator"):
        i = src.find(needle)
        if i != -1:
            flip_idx = i if flip_idx == -1 else min(flip_idx, i)
    # The BRAIN-85 short-circuit checks the fingerprint and
    # returns early with `reused: True`.
    short_circuit_idx = src.find('"reused": True')
    if short_circuit_idx == -1:
        short_circuit_idx = src.find("'reused': True")
    assert flip_idx != -1
    assert short_circuit_idx != -1
    assert short_circuit_idx < flip_idx, (
        "BRAIN-88 regression: BRAIN-85 short-circuit must "
        "return BEFORE the pending-flip fires. Identical-"
        "profile resubmits should leave durable state alone."
    )


def test_pending_flip_clears_prior_ready_metadata():
    """Source-level: the flip mutator must clear
    `_dna_completed_at`, `_dna_error`, `_dna_failed_at` so
    status doesn't surface stale derived metadata during
    regeneration."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    # Find the flip mutator's body (~700 chars).
    flip_idx = -1
    for needle in ("_pending_flip_mutator", "_dna_pending_mutator",
                   "_early_pending_mutator", "_retrain_flip_mutator"):
        i = src.find("def " + needle)
        if i != -1:
            flip_idx = i
            break
    assert flip_idx != -1
    block = src[flip_idx:flip_idx + 2000]
    assert '"_dna_state"' in block and '"pending"' in block, (
        "BRAIN-88 regression: flip mutator must set "
        "_dna_state=pending."
    )
    assert "_dna_completed_at" in block, (
        "BRAIN-88 regression: flip must clear "
        "_dna_completed_at so status doesn't show a stale "
        "completion timestamp during regeneration."
    )


def test_pending_flip_honors_revision_and_epoch_guards():
    """Source-level: the flip mutator must respect BRAIN-14
    (revision) + BRAIN-81 (epoch) guards. A stale tab can't
    flip pending past those checks just like it can't write
    new answers."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    flip_idx = -1
    for needle in ("_pending_flip_mutator", "_dna_pending_mutator",
                   "_early_pending_mutator", "_retrain_flip_mutator"):
        i = src.find("def " + needle)
        if i != -1:
            flip_idx = i
            break
    assert flip_idx != -1
    block = src[flip_idx:flip_idx + 2000]
    assert "_wizard_revision" in block, (
        "BRAIN-88 regression: flip must check _wizard_revision "
        "against captured value. Stale-write protection."
    )
    assert "_wizard_epoch" in block, (
        "BRAIN-88 regression: flip must check _wizard_epoch "
        "against captured value. Reset-boundary protection."
    )
