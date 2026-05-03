"""Regression test for BRAIN-14 (a375): optimistic-concurrency
revision guard on /api/wizard/complete to detect "stale derived
artifacts" race.

Failure pattern (per GPT-5.4 audit, this session):
- User clicks Complete-training on wizard revision N.
- Server captures inputs, starts brain+dossier build, kicks off
  background DNA generation.
- User edits an answer in another tab → /api/wizard/save-progress
  writes revision N+1.
- Old in-flight Complete commits derived artifacts (brain/dossier
  + team seed + DNA) based on STALE pre-edit inputs.
- The newer answers the user just made are silently lost from the
  derived artifacts even though they're preserved in
  _wizard_answers.

Optimistic concurrency: bump _wizard_revision on every save-progress;
complete captures revision at start, aborts the merge with 409 if
the revision moved during the brain-build window. Source-level test.
"""
from __future__ import annotations
import inspect


def test_save_progress_bumps_wizard_revision():
    """Source-level: the save-progress mutator must increment
    `_wizard_revision`. Without it, complete has no signal that
    answers changed during its long window."""
    from server import api_wizard_save_progress
    src = inspect.getsource(api_wizard_save_progress)
    assert "_wizard_revision" in src, (
        "BRAIN-14 regression: save-progress must bump "
        "`_wizard_revision` so that complete can detect stale-write "
        "races. Without it, optimistic concurrency is impossible."
    )


def test_complete_captures_and_checks_wizard_revision():
    """Source-level: complete must capture revision at start AND
    check it inside the merge mutator. Both halves are needed —
    capture without check is meaningless, check without capture is
    impossible."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    assert "_wizard_revision" in src, (
        "BRAIN-14 regression: complete must reference "
        "`_wizard_revision` to detect stale-write races during the "
        "brain-build window."
    )


def test_complete_returns_409_on_stale_write():
    """Source-level: the stale-write branch must return HTTP 409
    (Conflict) — the standard status code for optimistic-concurrency
    rejections, distinct from 429 (rate limit) so the frontend can
    show a different toast: 'Refresh — your answers changed in
    another tab.'"""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    assert "409" in src, (
        "BRAIN-14 regression: stale-write branch must return HTTP "
        "409 so the frontend can distinguish rate-limit (429) from "
        "version-mismatch and prompt the user to refresh."
    )
