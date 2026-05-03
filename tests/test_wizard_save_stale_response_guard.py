"""Regression test for BRAIN-67 (a428): client-side stale-async-response
guard on the wizard's save-progress / Continue handler.

Failure mode (per GPT-5.4 audit on async wizard race class):
- User clicks Continue on the wizard. The handler starts an
  /api/wizard/save-progress fetch. Because async event handlers
  don't block subsequent click events, a rapid second click spawns
  a SECOND concurrent handler with overlapping state.
- Each handler's `_saveOk` independently gates `_brainState.qi += 1`.
  Two successful saves → qi advances TWICE → the user silently
  skips a question (or jumps a phase). Their answer to the next
  question is never collected, but the wizard pretends it was.
- Plus: the second handler's "Saving…" status text overwrites the
  first handler's "Could not save: …" error before the user can
  read it. Operator never learns the save failed.

Server-side hardening (BRAIN-3 monotonic phase + BRAIN-6 atomic
merge_settings + BRAIN-14 _wizard_revision guard) prevents the
SERVER state from corrupting, but the CLIENT-side race still
causes user-visible skipped questions and lost validation toasts.

Defense in depth, both pulled directly from GPT-5.4's
async-race recommendations:
  1. Disable Continue/Skip/Back buttons while a save is in flight.
  2. Stamp every save with a monotonically-increasing token. When
     the response arrives, drop it on the floor if a newer save
     has been issued.

Source-level test — the wizard handler is inline JS in
`templates/jarvis.html`, so we inspect the HTML source for the
guard tokens.
"""
from __future__ import annotations
import re


def _wizard_html() -> str:
    with open("templates/jarvis.html", "r", encoding="utf-8") as fh:
        return fh.read()


def _continue_handler_block() -> str:
    """The Continue handler is uniquely identified by the
    `_brainSaveSeq` token introduced by the BRAIN-67 fix — that
    token is the load-bearing guard, so anchoring on it picks the
    correct block deterministically (not the scan handler's
    fire-and-forget save which deliberately doesn't need this guard
    because there's no `qi += 1` immediately gated on its result)."""
    src = _wizard_html()
    anchor = "_brainSaveSeq"
    idx = src.find(anchor)
    assert idx != -1, (
        "BRAIN-67 regression: `_brainSaveSeq` token missing entirely "
        "— Continue handler has no stale-response protection."
    )
    return src[max(0, idx - 800):idx + 3500]


def test_continue_handler_disables_buttons_during_save():
    """The Continue handler must disable the navigation buttons
    immediately on entry so a rapid second click can't spawn a
    parallel handler. Re-enable in the finally block."""
    block = _continue_handler_block()
    assert "next.disabled = true" in block, (
        "BRAIN-67 regression: Continue handler must disable the "
        "next button before issuing the save fetch — otherwise rapid "
        "double-click spawns parallel handlers and `qi` advances "
        "twice, silently skipping a question."
    )
    assert "next.disabled = false" in block, (
        "BRAIN-67 regression: Continue handler must re-enable the "
        "next button in the finally block so the user can advance "
        "after a successful save."
    )


def test_save_response_guarded_by_monotonic_token():
    """The save fetch must stamp a monotonically-increasing token
    and check it against the latest sequence before applying the
    response. This covers the edge case where the disable-toggle
    races with a queued click event."""
    block = _continue_handler_block()
    assert "_brainSaveSeq" in block, (
        "BRAIN-67 regression: every save-progress request must be "
        "stamped with a monotonically-increasing token so that "
        "stale responses can be ignored. Without this, a slow "
        "first response can clobber a fast second response's "
        "successful save and re-render with stale state."
    )
    # The token must be CHECKED, not just incremented.
    assert re.search(
        r"_myTok\s*!==\s*window\._brainSaveSeq",
        block,
    ), (
        "BRAIN-67 regression: the response handler must compare "
        "the captured token against the latest sequence and bail "
        "early if a newer save started — otherwise the token is "
        "just decoration."
    )


def test_save_in_flight_does_not_clobber_navstatus_on_stale():
    """When a stale response is dropped, it must NOT overwrite
    navStatus / advance qi — the early `return` after the token
    mismatch is the load-bearing guard."""
    block = _continue_handler_block()
    # The pattern we want: the token-mismatch branch returns BEFORE
    # touching navStatus / `_saveOk` / `qi`.
    mismatch_pattern = re.compile(
        r"if\s*\(\s*_myTok\s*!==\s*window\._brainSaveSeq\s*\)\s*\{[^}]*return",
        re.DOTALL,
    )
    assert mismatch_pattern.search(block), (
        "BRAIN-67 regression: the stale-response branch must "
        "`return` early — falling through into the navStatus / "
        "_saveOk / qi mutations would defeat the guard and let "
        "stale responses corrupt UI state."
    )
