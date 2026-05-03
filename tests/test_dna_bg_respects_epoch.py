"""Regression tests for BRAIN-82 (a448): _gen_dna background
closure must capture epoch at spawn time + discard terminal
write if reset has happened since.

Failure mode (per GPT-5.4 durable-workflow-stale-write audit):

After BRAIN-78 (durable DNA state) and BRAIN-80 (durable reset),
a sequence is now possible:

1. User clicks Complete → merge sets _dna_state="pending",
   _wizard_epoch=N. _spawn_bg(_gen_dna()).
2. User clicks Reset → /api/wizard/reset wipes wizard,
   _wizard_epoch=N+1.
3. Earlier _gen_dna closure finishes (slow provider, 10-30s).
   It writes _dna_state="ready" + _dna_completed_at via
   merge_settings — but the wizard was reset to a new epoch.
   The "ready" write resurrects derived state into a wizard
   that no longer exists.

The user sees /api/wizard/status flip from clean unset back to
ready. The BRAIN-79 agent gate then thinks DNA is available
for a wizard run that doesn't exist.

Per durable-workflow guidance: a background result may only
commit if it still belongs to the current generation.

Invariants:
- `_gen_dna` closure captures `_wizard_epoch` at spawn time.
- Both `_ready_mutator` and `_failed_mutator` compare current
  epoch vs captured. Mismatch → return cur unchanged (skip
  the write).
- SSE event still fires (best-effort) so any live UI listener
  can hear the result, even though the durable write is
  skipped.
"""
from __future__ import annotations
import inspect


def test_gen_dna_captures_epoch_at_spawn():
    """Source-level: api_wizard_complete must capture
    `_wizard_epoch` (or equivalent) BEFORE spawning _gen_dna,
    so the closure can compare against current epoch later."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    has_capture = (
        "_dna_spawn_epoch" in src
        or "_captured_dna_epoch" in src
        or "_dna_epoch_at_spawn" in src
        or "spawn_epoch" in src
    )
    assert has_capture, (
        "BRAIN-82 regression: _gen_dna must capture epoch at "
        "spawn time. Without it, the closure has no way to "
        "detect a reset that happened mid-generation."
    )


def test_dna_ready_mutator_skips_on_epoch_mismatch():
    """Source-level: the ready-state merge mutator must
    compare current epoch to captured. If different, return
    cur unchanged (skip the write)."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    # Look for the ready_mutator's epoch check.
    rm_idx = src.find("def _ready_mutator")
    assert rm_idx != -1
    # Take the next ~700 chars (the mutator body).
    block = src[rm_idx:rm_idx + 1500]
    has_epoch_compare = (
        "_wizard_epoch" in block
        or "_cur_epoch" in block
        or "spawn_epoch" in block
    )
    assert has_epoch_compare, (
        "BRAIN-82 regression: _ready_mutator must compare "
        "current epoch to captured spawn epoch. Without it, a "
        "reset mid-generation lets ready resurrect derived "
        "state into a wiped wizard."
    )


def test_dna_failed_mutator_skips_on_epoch_mismatch():
    """Source-level: same constraint as ready — failed must
    also bail on epoch mismatch. A failed-write into a reset
    wizard would persist a misleading 'failed' state on a
    wizard that doesn't exist."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    fm_idx = src.find("def _failed_mutator")
    assert fm_idx != -1
    block = src[fm_idx:fm_idx + 1500]
    has_epoch_compare = (
        "_wizard_epoch" in block
        or "_cur_epoch" in block
        or "spawn_epoch" in block
    )
    assert has_epoch_compare, (
        "BRAIN-82 regression: _failed_mutator must also bail "
        "on epoch mismatch. Both terminal states are durable "
        "writes; both need the gate."
    )


def test_epoch_captured_BEFORE_spawn_bg():
    """Source-level: the epoch capture must happen BEFORE
    `_spawn_bg(_gen_dna())` is called. The closure binds the
    captured value via lexical scope; capturing AFTER spawn
    would be a no-op (the bg task already started with a
    different snapshot)."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    spawn_idx = src.find("_spawn_bg(_gen_dna())")
    # Find any of the candidate capture variable names.
    capture_idx = -1
    for needle in ("_dna_spawn_epoch", "_captured_dna_epoch",
                   "_dna_epoch_at_spawn", "spawn_epoch"):
        i = src.find(needle)
        if i != -1:
            capture_idx = i if capture_idx == -1 else min(capture_idx, i)
    assert spawn_idx != -1
    assert capture_idx != -1
    assert capture_idx < spawn_idx, (
        "BRAIN-82 regression: epoch capture must precede "
        "_spawn_bg. Otherwise the closure has nothing to "
        "compare against."
    )
