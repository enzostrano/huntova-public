"""Regression tests for BRAIN-125 (a494): the
`wizard_reset` 410 and `stale_revision` 409 responses
on `/api/wizard/complete` must carry the same
`answers_applied: false` + reconciliation contract
that BRAIN-124 added to `dna_in_flight` 409.

Failure mode (Per Huntova engineering review on
conflict-response contract parity):

BRAIN-124 (a493) fixed the silent-discard bug on the
`dna_in_flight` 409 branch. The two SIBLING conflict
branches in the same flip mutator path still have the
old silent-discard contract:

- **stale_revision** (HTTP 409): a sibling tab edited
  the wizard between this tab's load and submit. The
  flip mutator sees `_cur_rev != _captured_revision`
  and short-circuits. The response says "Your wizard
  answers changed during training. Refresh and click
  Complete training again." — same lost-update class
  as dna_in_flight, but no `answers_applied: false`
  flag and no reconciliation tokens.
- **wizard_reset** (HTTP 410): a sibling tab clicked
  Reset, bumping `_wizard_epoch`. The response says
  "Wizard was reset elsewhere. Reload to start
  fresh." — again same class, again no explicit
  contract.

Per Huntova engineering review on conflict-response
contract parity: every wizard write rejection caused
by concurrent state change must explicitly state
whether submitted answers were applied AND provide
the reconciliation tokens the client needs. 409, 410,
and stale-write branches should share the same core
conflict contract so clients don't have to infer
data-loss semantics from status-code folklore.

Invariants:
- All three rejection branches (`wizard_reset` 410,
  `stale_revision` 409, `dna_in_flight` 409) include
  `answers_applied: false`.
- All three include `wizard_revision` + `wizard_epoch`
  for client reconciliation (captured from inside the
  flip mutator into `_flip_stale`).
- The user-facing error string explicitly mentions
  the answers were not saved.
"""
from __future__ import annotations
import inspect
import re


def _extract_branch(src: str, kind: str) -> str:
    """Extract the response block for a given _flip_stale
    kind ('wizard_reset', 'stale_revision', 'dna_in_flight').
    For wizard_reset and dna_in_flight, the branch is the
    standard `if _flip_stale["kind"] == "..."` form.
    For stale_revision, it's the fall-through `return
    JSONResponse(...)` after the other branches."""
    if kind == "stale_revision":
        # The fall-through stale_revision branch: starts
        # after the dna_in_flight branch closes and ends
        # at the `error_kind: "stale_revision"` closer.
        m = re.search(
            r'"in_flight_started_at"[^)]*\)[^,]*,\s*\}\s*,\s*status_code=409,\s*\)\s*(.*?status_code=409)',
            src,
            re.DOTALL,
        )
        if not m:
            # Fallback: search for the stale_revision block
            # generically.
            m = re.search(
                r'"error_kind"\s*:\s*"stale_revision"(.*?)(\)\s*$|\)\s*\n\s*[a-zA-Z])',
                src,
                re.DOTALL,
            )
            return m.group(0) if m else ""
        return m.group(0)
    pattern = (
        rf'if _flip_stale\["kind"\]\s*==\s*"{kind}":(.*?)status_code=\d+'
    )
    m = re.search(pattern, src, re.DOTALL)
    return m.group(0) if m else ""


def _branch_contains(src: str, kind: str, needle: str) -> bool:
    """Source-level: in the response area for `kind`,
    check that `needle` appears."""
    block = _extract_branch(src, kind)
    return needle in block


def test_wizard_reset_branch_has_answers_applied_flag():
    """410 wizard_reset must carry `answers_applied: false`."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    block = _extract_branch(src, "wizard_reset")
    assert block, "wizard_reset branch should still exist"
    assert "answers_applied" in block, (
        "BRAIN-125 regression: wizard_reset 410 must "
        "include `answers_applied: false` for parity "
        "with dna_in_flight 409. Same lost-update class."
    )


def test_wizard_reset_branch_includes_reconciliation_tokens():
    """410 wizard_reset must include current
    `wizard_revision` + `wizard_epoch` so the client
    can reconcile after a sibling-tab reset."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    block = _extract_branch(src, "wizard_reset")
    assert "wizard_epoch" in block, (
        "BRAIN-125 regression: wizard_reset 410 must "
        "include `wizard_epoch` (the new epoch the "
        "sibling reset moved to) so the client can "
        "detect the boundary."
    )
    assert "wizard_revision" in block, (
        "BRAIN-125 regression: wizard_reset 410 must "
        "include `wizard_revision` for completeness — "
        "even on reset, the new epoch may have a known "
        "starting revision."
    )


def test_wizard_reset_message_states_answers_not_saved():
    """410 wizard_reset error string must explicitly say
    the user's answers were not saved."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    block = _extract_branch(src, "wizard_reset")
    has_explicit_warning = (
        "not saved" in block.lower()
        or "not applied" in block.lower()
        or "discarded" in block.lower()
        or "weren't" in block.lower()
        or "did not save" in block.lower()
        or "wasn't saved" in block.lower()
    )
    assert has_explicit_warning, (
        "BRAIN-125 regression: wizard_reset 410 message "
        "must explicitly state the user's answers were "
        "not saved. The pre-fix copy 'Reload to start "
        "fresh' didn't tell the user their just-typed "
        "answers were dropped."
    )


def test_stale_revision_branch_has_answers_applied_flag():
    """409 stale_revision must carry `answers_applied: false`."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    # The stale_revision branch is the fall-through
    # JSONResponse after the dna_in_flight branch. Look
    # for a block containing both "stale_revision" and
    # the surrounding response shape.
    # Find the LAST occurrence of "stale_revision" so we
    # snap to the response branch's `error_kind`, not
    # the mutator branch's `_flip_stale["kind"]`.
    idx = src.rfind('"stale_revision"')
    assert idx >= 0, "stale_revision branch should exist"
    # Window backward from the error_kind to capture the
    # whole JSONResponse dict.
    block = src[max(0, idx - 2500):idx + 500]
    assert "answers_applied" in block, (
        "BRAIN-125 regression: stale_revision 409 must "
        "include `answers_applied: false` for parity "
        "with dna_in_flight 409. Same lost-update class — "
        "the user's submitted answers were rejected."
    )


def test_stale_revision_branch_includes_reconciliation_tokens():
    """409 stale_revision must include the live
    `wizard_revision` so the client knows the actual
    current value vs the one it captured."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    # Find the LAST occurrence of "stale_revision" so we
    # snap to the response branch's `error_kind`, not
    # the mutator branch's `_flip_stale["kind"]`.
    idx = src.rfind('"stale_revision"')
    block = src[max(0, idx - 2500):idx + 500]
    assert "wizard_revision" in block, (
        "BRAIN-125 regression: stale_revision 409 must "
        "include the live `wizard_revision` so the "
        "client can compare its captured value to the "
        "actual one and reconcile."
    )


def test_stale_revision_message_states_answers_not_saved():
    """409 stale_revision error string must explicitly
    state the answers were not saved."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    # Find the LAST occurrence of "stale_revision" so we
    # snap to the response branch's `error_kind`, not
    # the mutator branch's `_flip_stale["kind"]`.
    idx = src.rfind('"stale_revision"')
    block = src[max(0, idx - 2500):idx + 500]
    has_explicit_warning = (
        "not saved" in block.lower()
        or "not applied" in block.lower()
        or "discarded" in block.lower()
        or "weren't" in block.lower()
        or "did not save" in block.lower()
        or "wasn't saved" in block.lower()
    )
    assert has_explicit_warning, (
        "BRAIN-125 regression: stale_revision 409 "
        "message must explicitly state the user's "
        "answers were not saved. The pre-fix copy "
        "'Refresh and click Complete training again' "
        "didn't tell the user their answers were "
        "dropped."
    )


def test_flip_mutator_captures_state_in_wizard_reset_branch():
    """Source-level: when the flip mutator short-circuits
    on wizard_reset, it must also capture
    current_revision / current_epoch into _flip_stale
    so the response can return reconciliation tokens
    without a second DB read (parity with the
    dna_in_flight branch)."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    m = re.search(
        r'_flip_stale\["kind"\]\s*=\s*"wizard_reset"(.*?)return cur',
        src,
        re.DOTALL,
    )
    assert m, "wizard_reset mutator branch missing"
    branch = m.group(1)
    assert (
        "current_revision" in branch
        or "current_epoch" in branch
    ), (
        "BRAIN-125 regression: wizard_reset mutator "
        "branch must capture current state into "
        "_flip_stale so the 410 response can return "
        "reconciliation tokens without a second DB "
        "round-trip."
    )


def test_flip_mutator_captures_state_in_stale_revision_branch():
    """Same for the stale_revision mutator branch."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    m = re.search(
        r'_flip_stale\["kind"\]\s*=\s*"stale_revision"(.*?)return cur',
        src,
        re.DOTALL,
    )
    assert m, "stale_revision mutator branch missing"
    branch = m.group(1)
    assert (
        "current_revision" in branch
        or "current_epoch" in branch
    ), (
        "BRAIN-125 regression: stale_revision mutator "
        "branch must capture current state into "
        "_flip_stale."
    )
