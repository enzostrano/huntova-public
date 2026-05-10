"""Regression tests for BRAIN-107 (a476): the Skip handler
must isolate state across phase boundaries — it captures and
persists only the current question's input, never values from
prior phases or shared mutable state.

Failure mode (Per Huntova engineering review on multistep
form state-machine isolation):

A multi-step wizard's Skip transition is a classic state-leak
seam. The current Skip handler is correct today:

- `inputEl` is rebound per `_brainRenderQuestion` call.
- Chips backing arrays use `selected.slice()` so each render
  gets a fresh closure-bound array, not a shared one.
- The captured value writes only to `_brainState.answers[q.id]`,
  where `q` is the question that's actually rendered.

But the failure mode is brittle:

- A future refactor that lifts `selected` to module scope
  for "performance" would leak prior-phase chips into the
  destination phase.
- A future refactor that lets the handler iterate over
  `_brainState.answers` ("for k in answers: ...") and
  copy stale prior-phase keys forward would silently
  contaminate the persisted profile with semantically
  wrong answers.
- A future refactor that introduces a shared `inputEl`
  module-level alias would link inputs across questions.

Each of these would be a quiet, correctness-shaped bug that
only surfaces when an operator notices wrong answers in a
user's saved profile after a Skip path. The right defense
is to PIN the invariant in tests: any future change that
moves Skip toward shared state fails loudly.

Invariants:
- Skip handler writes EXACTLY to `_brainState.answers[q.id]`,
  not to any other key.
- The chips `selected` backing array is per-render
  (`initial.slice()`), not shared.
- The Skip handler does NOT iterate over
  `_brainState.answers` reading or writing other keys.
- Continue handler shares the same per-question key
  isolation.
"""
from __future__ import annotations
import re


def _wizard_html() -> str:
    with open("templates/jarvis.html", "r", encoding="utf-8") as fh:
        return fh.read()


def _skip_handler_block() -> str:
    """Return the full Skip handler body — from
    `skip.addEventListener('click', async () => {` to its
    closing `});`."""
    src = _wizard_html()
    start = src.find("skip.addEventListener('click', async () => {")
    assert start != -1, "Skip handler not found"
    # Find a reasonable closing window — Skip handler ends
    # before the next major DOM construction (~3000 chars).
    return src[start:start + 4000]


def test_skip_handler_writes_only_to_current_qid():
    """Source-level: the only assignment to
    `_brainState.answers[…]` inside the Skip handler must be
    `_brainState.answers[q.id] = captured` (or equivalent)."""
    block = _skip_handler_block()
    # Find every `_brainState.answers[` assignment.
    assignments = re.findall(
        r"_brainState\.answers\[[^\]]+\]\s*=", block
    )
    assert assignments, (
        "BRAIN-107 sanity: Skip handler should at least write "
        "the captured value to _brainState.answers[q.id]."
    )
    for a in assignments:
        # Each must target `q.id`, NOT a different key like
        # `q.phase`, a hardcoded id, or a loop variable.
        assert "[q.id]" in a, (
            f"BRAIN-107 regression: Skip handler writes to "
            f"`{a}` — that's not `_brainState.answers[q.id]`. "
            f"Phase-isolation broken: a future refactor is "
            f"writing to a different key, which can leak "
            f"values across phases."
        )


def test_skip_handler_does_not_iterate_over_answers():
    """Source-level: the Skip handler must NOT contain a
    loop over `_brainState.answers` reading or copying
    values across keys. Cross-key iteration is the canonical
    way to leak phase-4 state into phase-5 fields."""
    block = _skip_handler_block()
    # Look for `for ... of _brainState.answers` or
    # `Object.keys(_brainState.answers)` patterns.
    patterns = [
        "for (const _ in _brainState.answers",
        "for (const k in _brainState.answers",
        "for (const key in _brainState.answers",
        "for (let k in _brainState.answers",
        "Object.keys(_brainState.answers)",
        "Object.entries(_brainState.answers)",
        "for (const [k, v] of _brainState.answers",
    ]
    for p in patterns:
        assert p not in block, (
            f"BRAIN-107 regression: Skip handler contains "
            f"`{p}` — iteration over the answers map is "
            f"how cross-phase leaks happen. Each Skip "
            f"transition should touch only the current "
            f"question's key."
        )


def test_chips_selected_uses_per_render_slice():
    """Source-level: the chips render path must initialize
    `selected` via `.slice()` (or equivalent fresh-copy)
    from the prior answer, NOT by aliasing the stored array.
    Aliasing would mean toggling chips in phase 4 mutates
    the persisted phase-4 answer in place — a leak class."""
    src = _wizard_html()
    # Find the chips render block.
    chips_idx = src.find("} else if (q.type === 'chips') {")
    assert chips_idx != -1, "chips render block missing"
    block = src[chips_idx:chips_idx + 1500]
    # Must use .slice() on the initial value to make a fresh
    # copy.
    has_slice = (
        "initial.slice()" in block
        or ".slice()" in block
    )
    assert has_slice, (
        "BRAIN-107 regression: chips render must initialize "
        "the `selected` backing array via `.slice()` (or "
        "another fresh-copy primitive). Aliasing the stored "
        "answer leaks toggle mutations into persisted state "
        "across phase boundaries."
    )


def test_continue_handler_writes_only_to_current_qid():
    """Companion guarantee on Continue (the other path that
    transitions phases). Same invariant: only writes to
    `_brainState.answers[q.id]`, never a different key."""
    src = _wizard_html()
    # Find the Continue handler.
    start = src.find("next.addEventListener('click', async () => {")
    assert start != -1
    block = src[start:start + 4000]
    assignments = re.findall(
        r"_brainState\.answers\[[^\]]+\]\s*=", block
    )
    for a in assignments:
        assert "[q.id]" in a, (
            f"BRAIN-107 regression: Continue handler writes "
            f"to `{a}` instead of `_brainState.answers[q.id]`. "
            f"Same phase-isolation invariant as Skip."
        )


def test_skip_handler_advances_qi_after_persist():
    """Source-level: Skip must advance `qi` AFTER capturing
    + persisting the current question's value. If `qi` were
    advanced first, the captured value would overwrite the
    NEW phase's slot — a clear leak path."""
    block = _skip_handler_block()
    # Find positions of the answers write and the qi advance.
    write_idx = block.find("_brainState.answers[q.id] =")
    advance_idx = block.find("_brainState.qi += 1")
    assert write_idx != -1
    assert advance_idx != -1
    assert write_idx < advance_idx, (
        "BRAIN-107 regression: Skip handler advances `qi` "
        "before persisting the captured value. That path "
        "would mis-route the captured text to the wrong "
        "phase's slot."
    )
