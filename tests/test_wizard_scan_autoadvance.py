"""Regression test for BRAIN-2 (a366): the scan click handler in
the brain wizard didn't auto-advance to the next question after a
successful scan. Every other question auto-advances on save, but
the URL question (always question 1) left users stranded — they
had to scroll + click Continue manually after the green ✓.

Source-level check (same approach as a365's phase-5 rate-limit
test): we read the inline JS in templates/jarvis.html and assert
the scan handler's success branch advances `_brainState.qi` and
fires a save-progress sync. Pure file-IO unit test, no Playwright
or browser harness needed.
"""
from __future__ import annotations

from pathlib import Path
import re


_REPO_ROOT = Path(__file__).resolve().parent.parent
_JARVIS = _REPO_ROOT / "templates" / "jarvis.html"


def _scan_handler_block() -> str:
    """Return the source of the scan button click handler — from
    `scanBtn.addEventListener('click'` through the matching closing
    `});`. We scope assertions to this region so unrelated mentions
    of `_brainState.qi` elsewhere in the file don't pollute the test."""
    src = _JARVIS.read_text()
    start = src.find("scanBtn.addEventListener('click'")
    assert start != -1, "scanBtn click handler missing entirely — test stale?"
    # Naive end: first occurrence of `_hideScanAnim();\n      });` after start.
    end_marker = "_hideScanAnim();\n      });"
    end = src.find(end_marker, start)
    assert end != -1, "scan handler closing brace not found"
    return src[start:end + len(end_marker)]


def test_scan_success_advances_question_index():
    block = _scan_handler_block()
    # The success branch (the else of `if (d && d.error)`) must bump qi.
    assert "_brainState.qi" in block, (
        "BRAIN-2 regression: scan success branch must advance "
        "_brainState.qi so the wizard moves past the URL question "
        "after a successful scan, matching every other question."
    )


def test_scan_success_persists_via_save_progress():
    """The auto-advance must also POST /api/wizard/save-progress so
    the URL + prefilled answers survive a reload. Without this, the
    user reloads and the wizard restarts from question 1 with the
    same blank URL field — same data-loss class as a363's Skip fix."""
    block = _scan_handler_block()
    assert "/api/wizard/save-progress" in block, (
        "BRAIN-2 regression: scan success must persist server-side "
        "before auto-advancing — otherwise reload loses both the URL "
        "and the prefilled answers."
    )


def test_scan_advance_is_in_success_branch_only():
    """Sanity check: the qi bump must be inside the `else` (success)
    branch, NOT before the `if (d && d.error)` check. A bug here
    would auto-advance even when the scan failed, leaving the user
    on the next question without prefilled data."""
    block = _scan_handler_block()
    err_idx = block.find("if (d && d.error)")
    qi_idx = block.find("_brainState.qi")
    assert err_idx != -1, "error branch check missing — test stale?"
    assert qi_idx != -1, "qi bump missing — first test should have caught this"
    assert qi_idx > err_idx, (
        "BRAIN-2 regression: qi bump must come AFTER the d.error "
        "branch (i.e. inside the success path), otherwise failed "
        "scans also auto-advance."
    )
