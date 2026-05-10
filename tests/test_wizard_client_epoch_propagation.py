"""Regression tests for BRAIN-83 (a452): the wizard JS must
capture `wizard_epoch` from /api/settings on load and forward
it as `expected_epoch` on every save-progress write. Without
client participation, the BRAIN-81 server-side enforcement is
dead code.

Failure mode (Per Huntova engineering review on optimistic-
concurrency end-to-end coverage):

BRAIN-81 added `_wizard_epoch` server-side and made
save-progress accept `expected_epoch`. Mismatch yields HTTP
410 with `error_kind: "wizard_reset"`. But the client wizard
JS only captured `_wizard_revision` — never `_wizard_epoch`.
Result: stale tabs from before a reset would post
save-progress with a valid (current) revision but no epoch
token, the server-side guard would skip (no `expected_epoch`
sent → no comparison), and the stale write would land into
the freshly reset wizard.

End-to-end optimistic concurrency requires the client to:
- Capture both tokens on every load.
- Forward both on every write.
- Update both from every successful response.
- Handle the 410 reset response with a reload (not a retry).

Invariants:
- `_brainState.epoch` populated on initial wizard load.
- `_brainState.epoch` populated on Re-train entry too.
- save-progress fetch body includes `expected_epoch` when
  client knows it.
- save-progress success response refreshes `_brainState.epoch`.
- 410 response triggers a distinct "wizard was reset, reloading"
  toast + an automatic location.reload(), not a generic 409 retry.
"""
from __future__ import annotations


def _wizard_html() -> str:
    with open("templates/jarvis.html", "r", encoding="utf-8") as fh:
        return fh.read()


def test_initial_load_captures_wizard_epoch():
    """The initial wizard-load path must read
    `w._wizard_epoch` from /api/settings and store it on
    _brainState."""
    src = _wizard_html()
    # Find the BRAIN-68 revision-capture block; the BRAIN-83
    # epoch capture must live right next to it.
    rev_idx = src.find("_brainState.revision = w._wizard_revision")
    assert rev_idx != -1
    block = src[max(0, rev_idx - 200):rev_idx + 800]
    assert "_brainState.epoch" in block, (
        "BRAIN-83 regression: the initial wizard-load handler "
        "must capture `w._wizard_epoch` into `_brainState.epoch`. "
        "Without it, expected_epoch can never be sent and the "
        "BRAIN-81 server-side guard is dead code."
    )


def test_retrain_entry_captures_wizard_epoch():
    """The Re-train entry path also captures revision; it must
    capture epoch too. Otherwise a user who clicked Re-train
    after a sibling-tab reset would write into the stale
    wizard."""
    src = _wizard_html()
    # The Re-train entry path is the second `_brainState.revision`
    # assignment. Find both, then assert the second has an epoch
    # capture nearby.
    first = src.find("_brainState.revision = w._wizard_revision")
    second = src.find("_brainState.revision = w._wizard_revision",
                      first + 1)
    assert first != -1 and second != -1
    block = src[second:second + 400]
    assert "_brainState.epoch" in block, (
        "BRAIN-83 regression: Re-train entry must also capture "
        "epoch."
    )


def test_save_progress_body_includes_expected_epoch():
    """The Continue handler's save-progress fetch body must
    include `expected_epoch` when the client knows it."""
    src = _wizard_html()
    anchor = "_brainSaveSeq"
    idx = src.find(anchor)
    assert idx != -1
    block = src[idx:idx + 5000]
    assert "expected_epoch" in block, (
        "BRAIN-83 regression: save-progress fetch body must "
        "include expected_epoch. Without it, the server cannot "
        "detect reset boundaries."
    )


def test_save_progress_success_updates_brainstate_epoch():
    """The success path must read `epoch` from the response
    and update `_brainState.epoch` so future writes stay
    aligned."""
    src = _wizard_html()
    anchor = "_brainSaveSeq"
    idx = src.find(anchor)
    block = src[idx:idx + 5000]
    # Look for `_d.epoch` referenced inside the success branch.
    assert "_d.epoch" in block or "d.epoch" in block, (
        "BRAIN-83 regression: success response must refresh "
        "`_brainState.epoch` from the server-returned epoch."
    )


def test_save_progress_handles_410_with_reload():
    """The 410 response (epoch mismatch / wizard reset) must
    trigger a reload — not the BRAIN-68 409 retry toast."""
    src = _wizard_html()
    anchor = "_brainSaveSeq"
    idx = src.find(anchor)
    block = src[idx:idx + 5000]
    assert "_r.status === 410" in block, (
        "BRAIN-83 regression: 410 must have its own branch in "
        "the save-progress response handler."
    )
    assert "location.reload" in block, (
        "BRAIN-83 regression: 410 path must auto-reload so the "
        "client converges on the reset wizard. Otherwise the "
        "user is stuck with stale local state."
    )
