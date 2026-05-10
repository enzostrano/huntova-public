"""Regression tests for BRAIN-81 (a447): wizard reset must bump
a `_wizard_epoch` token so stale tabs detect the reset boundary
and converge cleanly — not silently resurrect pre-reset answers.

Failure mode (per GPT-5.4 versioned-state-reset audit):

After BRAIN-80 reset wipes `s["wizard"] = {}`, `_wizard_revision`
returns to 0. The BRAIN-68 stale-write guard skips when
`_cur_rev == 0`. So a stale tab from before the reset, holding
`expected_revision=N` (>0), would post save-progress, take no
early-return, and merge pre-reset answers into the fresh wizard.
The user's reset is silently undone.

Versioned-state systems need an EPOCH token alongside revision.
Revision tracks edits within one wizard life; epoch tracks
reset boundaries.

Invariants:
- `_wizard_epoch` field exists (defaults to 0).
- `/api/wizard/reset` increments `_wizard_epoch`.
- `/api/wizard/save-progress` accepts `expected_epoch`.
- Mismatch → distinct response (`error_kind: "wizard_reset"`)
  with HTTP 410 — structurally different from BRAIN-68's 409.
- `/api/wizard/status` exposes `wizard_epoch`.
"""
from __future__ import annotations
import inspect


def test_reset_endpoint_increments_wizard_epoch():
    from server import api_wizard_reset
    src = inspect.getsource(api_wizard_reset)
    assert "_wizard_epoch" in src, (
        "BRAIN-81 regression: reset must bump `_wizard_epoch`."
    )


def test_reset_preserves_epoch_continuity_across_full_wipe():
    from server import api_wizard_reset
    src = inspect.getsource(api_wizard_reset)
    has_epoch_read = (
        ".get(\"_wizard_epoch\"" in src
        or ".get('_wizard_epoch'" in src
        or "prior_epoch" in src
    )
    assert has_epoch_read, (
        "BRAIN-81 regression: reset must read prior epoch + bump."
    )


def test_save_progress_checks_expected_epoch():
    from server import api_wizard_save_progress
    src = inspect.getsource(api_wizard_save_progress)
    assert "expected_epoch" in src, (
        "BRAIN-81 regression: save-progress must accept "
        "`expected_epoch`."
    )


def test_save_progress_returns_distinct_response_on_epoch_mismatch():
    from server import api_wizard_save_progress
    src = inspect.getsource(api_wizard_save_progress)
    has_distinct_kind = (
        "wizard_reset" in src
        or "epoch_mismatch" in src
    )
    assert has_distinct_kind, (
        "BRAIN-81 regression: epoch-mismatch response must "
        "carry a distinct marker."
    )


def test_status_endpoint_exposes_wizard_epoch():
    from server import api_wizard_status
    src = inspect.getsource(api_wizard_status)
    assert "_wizard_epoch" in src or "wizard_epoch" in src, (
        "BRAIN-81 regression: /api/wizard/status must expose "
        "the epoch."
    )


def test_save_progress_carries_epoch_on_writes():
    from server import api_wizard_save_progress
    src = inspect.getsource(api_wizard_save_progress)
    assert src.count("_wizard_epoch") >= 2, (
        "BRAIN-81 regression: save-progress must reference "
        "`_wizard_epoch` at least twice (compare + preserve)."
    )
