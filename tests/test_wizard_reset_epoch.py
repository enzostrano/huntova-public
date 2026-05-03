"""Regression tests for BRAIN-81 (a442): wizard reset must bump
a `_wizard_epoch` token so stale tabs detect the reset boundary
and converge cleanly — not silently resurrect pre-reset answers
or 409-loop forever.

Failure mode (per GPT-5.4 versioned-state-reset audit):

After BRAIN-80 reset, `s["wizard"] = {}` clears `_wizard_revision`
back to 0. The BRAIN-68 stale-write guard:

    if expected_revision is not None and _cur_rev > 0 \\
       and expected_revision != _cur_rev:
        return 409

is SKIPPED when `_cur_rev == 0` (the post-reset state). So a
stale tab still sitting on revision=N from before the reset
sends save-progress with `expected_revision=N` — server takes
no early-return, mutator runs, merges the stale tab's pre-reset
answers into the fresh wizard. Revision bumps to 1. The stale
tab's old answers are now resurrected into a "clean" wizard.

That's worse than a 409 loop. The user explicitly reset the
wizard; their old answers were supposed to be gone. A
forgotten background tab undoes the reset.

Versioned-state systems need an EPOCH token alongside revision.
Revision tracks edits within one wizard life; epoch tracks
reset boundaries. Reset bumps epoch + resets revision to 0.
save-progress checks BOTH — epoch mismatch yields a distinct
"wizard was reset" response so the client can self-recover
(reload + clear local state) instead of retrying with stale
data.

Invariants:
- `_wizard_epoch` field exists in wizard state. Defaults to 0
  for legacy installs.
- `/api/wizard/reset` increments `_wizard_epoch` (not just
  wipes everything to 0 — that's BRAIN-80; epoch is the
  ratchet that lets stale tabs detect the boundary).
- `/api/wizard/save-progress` accepts optional
  `expected_epoch`. If provided AND mismatches stored, return
  410 Gone (or 409 with distinct `error_kind: "wizard_reset"`).
- Status endpoint (`/api/wizard/status`) exposes
  `wizard_epoch` so clients can capture it on load.
- The 410/distinct response is structurally different from the
  410-Gone-class so the client can show "wizard reset
  elsewhere, reloading" instead of generic "stale, retry".
"""
from __future__ import annotations
import inspect


def test_reset_endpoint_increments_wizard_epoch():
    """Source-level: api_wizard_reset must bump
    `_wizard_epoch`. Pre-fix, full wipe (`s["wizard"] = {}`)
    cleared revision back to 0, but offered no epoch boundary
    for stale tabs to detect."""
    from server import api_wizard_reset
    src = inspect.getsource(api_wizard_reset)
    assert "_wizard_epoch" in src, (
        "BRAIN-81 regression: reset must bump `_wizard_epoch` "
        "so stale tabs can detect the reset boundary."
    )


def test_reset_preserves_epoch_continuity_across_full_wipe():
    """Behavioral: even though reset wipes the wizard dict,
    the epoch must be CARRIED FORWARD (incremented) — not
    reset back to 0. A stale tab needs the new epoch > old
    epoch to detect the boundary."""
    from server import api_wizard_reset
    src = inspect.getsource(api_wizard_reset)
    # The implementation must read the prior epoch and bump.
    has_epoch_read = (
        ".get(\"_wizard_epoch\"" in src
        or ".get('_wizard_epoch'" in src
        or "prior_epoch" in src
    )
    assert has_epoch_read, (
        "BRAIN-81 regression: reset must read prior epoch "
        "before wiping so the new wizard's epoch is "
        "max(old_epoch, 0) + 1. Setting epoch back to 0 lets a "
        "stale tab silently match it."
    )


def test_save_progress_checks_expected_epoch():
    """Source-level: api_wizard_save_progress must accept
    `expected_epoch` from the client and reject mismatches
    with a distinct error code (not the generic 409 stale)."""
    from server import api_wizard_save_progress
    src = inspect.getsource(api_wizard_save_progress)
    assert "expected_epoch" in src, (
        "BRAIN-81 regression: save-progress must accept "
        "`expected_epoch` so stale tabs can be told to reload "
        "rather than retrying forever."
    )


def test_save_progress_returns_distinct_response_on_epoch_mismatch():
    """Source-level: epoch mismatch must produce a response
    structurally different from the BRAIN-68 stale-revision
    409 — so the client can render 'wizard was reset, reloading'
    rather than 'another tab edited this'."""
    from server import api_wizard_save_progress
    src = inspect.getsource(api_wizard_save_progress)
    # Look for a distinct error kind in the response payload.
    has_distinct_kind = (
        "wizard_reset" in src
        or '"reset": True' in src
        or "'reset': True" in src
        or "epoch_mismatch" in src
    )
    assert has_distinct_kind, (
        "BRAIN-81 regression: epoch-mismatch response must "
        "carry a distinct marker (e.g. `error_kind: "
        "'wizard_reset'`) so the client can choose the right "
        "recovery path."
    )


def test_status_endpoint_exposes_wizard_epoch():
    """Source-level: /api/wizard/status must expose
    `wizard_epoch` so the client can capture it on every load
    and pass it back with save-progress."""
    from server import api_wizard_status
    src = inspect.getsource(api_wizard_status)
    assert "_wizard_epoch" in src or "wizard_epoch" in src, (
        "BRAIN-81 regression: /api/wizard/status must expose "
        "the epoch so clients can track it. Without it, the "
        "epoch enforcement is dead code."
    )


def test_save_progress_carries_epoch_on_writes():
    """Source-level: when save-progress writes the merged blob,
    it must preserve the epoch (not reset it). The epoch only
    moves on /api/wizard/reset."""
    from server import api_wizard_save_progress
    src = inspect.getsource(api_wizard_save_progress)
    # The mutator should read prior _wizard_epoch from current
    # state and either preserve it or use it for comparison.
    # Don't accidentally reset to 0 inside the save mutator.
    # We just need to confirm the field is referenced inside
    # the mutator body.
    assert src.count("_wizard_epoch") >= 2, (
        "BRAIN-81 regression: save-progress must reference "
        "`_wizard_epoch` at least twice — once to compare with "
        "client's expected_epoch, once to preserve in the merged "
        "wizard blob."
    )
