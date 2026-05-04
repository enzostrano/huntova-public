"""Regression tests for BRAIN-84 (a453): the Skip handler and the
scan-success post-persist callsite must also forward `expected_revision`
+ `expected_epoch` like the Continue handler did in BRAIN-83.

Failure mode (Per Huntova engineering review on optimistic-concurrency
end-to-end coverage gap):

BRAIN-83 (a452) wired the Continue handler's save-progress fetch to
forward both tokens. But the wizard's `templates/jarvis.html` has
THREE save-progress callsites:

1. Continue handler (~line 4600) — covered by BRAIN-83.
2. Scan-success post-persist (~line 4385) — fires after the URL scan
   prefills answers; auto-advance writes the prefilled answers to the
   server before bumping `qi`.
3. Skip handler (~line 4714) — fires when the user clicks Skip
   without filling in the current question.

Pre-fix, callsites 2 and 3 sent only `{answers, phase}` — no
`expected_revision`, no `expected_epoch`. So the BRAIN-68 stale-revision
guard and the BRAIN-81 reset-epoch guard both skipped against those
writes. A stale tab clicking Skip in a wizard that was reset elsewhere
would silently resurrect its pre-reset answers — exactly the bug
BRAIN-83 fixed for Continue, but on a different click path.

Invariants (mirrors BRAIN-83):
- Every save-progress fetch body in `templates/jarvis.html` includes
  `expected_revision` when known.
- Every save-progress fetch body includes `expected_epoch` when known.
- (Out of scope: response-handling parity. Scan-success and Skip are
  fire-and-forget by design — see comments in the source explaining
  that posture. They don't auto-reload on 410 yet, but the SERVER
  rejects the write either way; the user's local state desync is
  small and self-corrects on next page load.)
"""
from __future__ import annotations
import re


def _wizard_html() -> str:
    with open("templates/jarvis.html", "r", encoding="utf-8") as fh:
        return fh.read()


def _all_save_progress_blocks() -> list[str]:
    """Return the ~400-char window around every fetch('/api/wizard/save-progress')
    callsite. The wizard has three (Continue / scan-success / Skip)."""
    src = _wizard_html()
    blocks: list[str] = []
    pattern = "fetch('/api/wizard/save-progress'"
    pos = 0
    while True:
        idx = src.find(pattern, pos)
        if idx == -1:
            break
        # Take the 400 chars before (where the body is constructed)
        # and 200 chars after (the response handling).
        blocks.append(src[max(0, idx - 600):idx + 600])
        pos = idx + len(pattern)
    return blocks


def test_save_progress_callsites_present():
    """Sanity: the wizard JS has multiple save-progress callsites
    (Continue, scan-success, Skip, plus BRAIN-87 added Back). The
    point of this test is to keep the contract uniform: every
    callsite must forward both tokens. If a future refactor
    consolidates them, relax this — don't drop the contract."""
    blocks = _all_save_progress_blocks()
    assert len(blocks) >= 3, (
        f"BRAIN-84 sanity: expected at least 3 save-progress "
        f"callsites; found {len(blocks)}."
    )


def test_every_save_progress_callsite_forwards_expected_revision():
    """Every save-progress write must include expected_revision."""
    blocks = _all_save_progress_blocks()
    for i, block in enumerate(blocks):
        assert "expected_revision" in block, (
            f"BRAIN-84 regression: save-progress callsite #{i} "
            f"omits expected_revision. The BRAIN-68 stale-revision "
            f"guard skips on this write — a stale tab can silently "
            f"clobber a sibling tab's newer state."
        )


def test_every_save_progress_callsite_forwards_expected_epoch():
    """Every save-progress write must include expected_epoch."""
    blocks = _all_save_progress_blocks()
    for i, block in enumerate(blocks):
        assert "expected_epoch" in block, (
            f"BRAIN-84 regression: save-progress callsite #{i} "
            f"omits expected_epoch. The BRAIN-81 reset-boundary "
            f"guard skips on this write — a stale tab post-reset "
            f"can silently resurrect pre-reset answers."
        )


def test_token_forwarding_is_conditional_on_known_value():
    """The forwarding must guard on `typeof === 'number'` so a
    fresh wizard (no token yet) doesn't send `undefined` and
    confuse the server's optional-token semantics."""
    blocks = _all_save_progress_blocks()
    for i, block in enumerate(blocks):
        # Look for the typeof guard adjacent to expected_epoch.
        assert re.search(
            r"typeof\s+_brainState\.epoch\s*===\s*'number'",
            block,
        ), (
            f"BRAIN-84 regression: save-progress callsite #{i} "
            f"must conditionally include expected_epoch only "
            f"when _brainState.epoch is known. Sending undefined "
            f"on a fresh wizard would force a no-op 410."
        )
