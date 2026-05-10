"""Regression tests for BRAIN-87 (a456): "furthest unlocked phase"
and "current viewing cursor" must be separately tracked. The
BRAIN-3 (a364) `_monotonic_phase` rule prevents stale-tab
regression on persisted MAX phase, but it must NOT prevent
legitimate in-session backward navigation.

Failure mode (Per Huntova engineering review on multi-step form
back-navigation):

Pre-fix:
1. User advances to q7 (qi=7). save-progress writes
   `_wizard_phase=7` (monotonic).
2. User clicks Back twice — qi=5 in local state.
3. User reloads. Client load reads `_wizard_phase=7` and force-
   sets `_brainState.qi = Math.min(w._wizard_phase, …)` = 7.
4. User snapped back to q7. Their backward navigation lost.

Same bug on every reload, every Re-train entry, every settings
fetch that rehydrates wizard state. The user can keep clicking
Back but the page-load path always undoes their navigation.

Standard fix (per multi-step form back-navigation guidance):
"furthest unlocked phase" (max ever reached) and "current
cursor" (currently viewing) are different state. Max phase
stays monotonic for stale-tab protection. Cursor moves freely
backward + forward within `[0, max_phase]`.

Invariants:
- Wizard state stores `_wizard_cursor` separately from
  `_wizard_phase`. Cursor is NOT monotonic.
- save-progress accepts an optional `cursor` body field and
  persists it.
- Status endpoint exposes `wizard_cursor`.
- Client load path (initial + Re-train) reads
  `w._wizard_cursor` first, falls back to `_wizard_phase` for
  backward compatibility with users on pre-BRAIN-87 state.
- Back handler triggers a save-progress that writes the new
  cursor.
"""
from __future__ import annotations
import inspect


def test_save_progress_accepts_cursor():
    """Source-level: api_wizard_save_progress must accept an
    optional `cursor` body field."""
    from server import api_wizard_save_progress
    src = inspect.getsource(api_wizard_save_progress)
    assert '"cursor"' in src or "'cursor'" in src or "body.get(\"cursor\")" in src or "body.get('cursor')" in src, (
        "BRAIN-87 regression: save-progress must accept a `cursor` "
        "body field so the client can persist its current viewing "
        "position separately from the monotonic max phase."
    )


def test_save_progress_persists_wizard_cursor():
    """Source-level: the merge mutator must persist
    `_wizard_cursor`."""
    from server import api_wizard_save_progress
    src = inspect.getsource(api_wizard_save_progress)
    assert "_wizard_cursor" in src, (
        "BRAIN-87 regression: save-progress mutator must persist "
        "`_wizard_cursor`. Without it, the cursor doesn't survive "
        "reload and the back-navigation fix is dead code."
    )


def test_save_progress_does_not_apply_monotonic_to_cursor():
    """Source-level: cursor must NOT pass through
    `_monotonic_phase`. The whole point is to allow backward
    movement."""
    from server import api_wizard_save_progress
    src = inspect.getsource(api_wizard_save_progress)
    # Find the cursor write line.
    cursor_idx = src.find("_wizard_cursor")
    assert cursor_idx != -1
    # Take ~150 chars around it; verify _monotonic_phase isn't
    # called on the cursor value.
    block = src[max(0, cursor_idx - 100):cursor_idx + 250]
    bad_pattern = "_monotonic_phase(w.get(\"_wizard_cursor\""
    assert bad_pattern not in block, (
        "BRAIN-87 regression: cursor must NOT use _monotonic_phase. "
        "The whole point is to allow backward movement."
    )


def test_status_endpoint_exposes_wizard_cursor():
    """Source-level: /api/wizard/status must expose
    `wizard_cursor` so the client can capture it on load."""
    from server import api_wizard_status
    src = inspect.getsource(api_wizard_status)
    assert "wizard_cursor" in src or "_wizard_cursor" in src, (
        "BRAIN-87 regression: /api/wizard/status must expose the "
        "cursor."
    )


def test_client_load_prefers_cursor_over_phase():
    """The wizard JS load path must read `_wizard_cursor` first
    and fall back to `_wizard_phase` only if cursor is absent
    (backward compat with pre-BRAIN-87 state)."""
    with open("templates/jarvis.html", "r", encoding="utf-8") as fh:
        src = fh.read()
    # The initial-load block reads `w._wizard_phase` to set
    # _brainState.qi. After BRAIN-87, it must consult cursor first.
    assert "w._wizard_cursor" in src, (
        "BRAIN-87 regression: client load must read "
        "`w._wizard_cursor` before falling back to `_wizard_phase`. "
        "Otherwise the server has the cursor but the client never "
        "reads it on reload."
    )


def test_back_handler_persists_cursor_via_save_progress():
    """Back handler must trigger a save-progress write so the
    cursor survives reload — otherwise the fix only works
    in-session."""
    with open("templates/jarvis.html", "r", encoding="utf-8") as fh:
        src = fh.read()
    # Find the back.addEventListener block.
    back_idx = src.find("back.addEventListener('click'")
    assert back_idx != -1
    block = src[back_idx:back_idx + 2000]
    # Must trigger persistence — either a fetch to save-progress
    # or a helper that does it.
    assert ("save-progress" in block or "_persistCursor" in block or
            "fetch('/api/wizard/save-progress'" in block), (
        "BRAIN-87 regression: Back handler must persist the new "
        "cursor to the server. Otherwise reloads lose the user's "
        "backward navigation."
    )
