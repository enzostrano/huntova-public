"""Regression tests for BRAIN-124 (a493): the 409
`dna_in_flight` response from
`/api/wizard/complete`'s BRAIN-110 atomic-claim loss
must explicitly tell the losing tab that its
submitted answers were NOT applied to the active
generation, and must include enough state for the
client to reconcile.

Failure mode (Per Huntova engineering review on
optimistic-concurrency conflict messaging + lost-
update semantics):

BRAIN-110 (a479) added the atomic claim that
prevents two tabs from both spawning `_gen_dna()`.
The losing tab gets HTTP 409 with:

```json
{
  "ok": false,
  "in_flight": true,
  "error": "Brain training is already running for
    this retrain in another tab. Wait for it to
    finish, or reload this tab to follow its
    progress.",
  "error_kind": "dna_in_flight"
}
```

The locking is correct. The MESSAGING isn't.

If two tabs typed different answers and submitted
near-simultaneously, the winner's answers feed into
the active DNA generation. The loser's answers are
DROPPED — they never reach merge_settings, never
update the row. The current copy says "Wait for it
to finish" — which incorrectly implies the loser's
submitted answers are part of the in-flight run.
They aren't. The user thinks their corrections went
through; they didn't.

Per Huntova engineering review on conflict
messaging + lost-update semantics: a 409 is only
correct when the response explicitly states that
the rejected write was not applied. Silent discard
turns correct concurrency control into a trust-
eroding UX bug.

Invariants:
- The 409 dna_in_flight response includes an
  explicit `answers_applied: false` (or equivalent)
  flag so the client can branch deterministically.
- The response includes the current `wizard_revision`
  and `wizard_epoch` so the client can reconcile via
  reload or merge.
- The error message tells the user their answers
  were NOT saved AND points them to the active run.
- The dna_pending state metadata (started_at) is
  surfaced so the UI can show countdown.
"""
from __future__ import annotations
import inspect


def _extract_response_block(src: str) -> str:
    """Pull out the dna_in_flight RESPONSE block (the
    JSONResponse, not the mutator's bookkeeping). Anchor
    on the response branch's `if _flip_stale["kind"]
    == "dna_in_flight":` line and read forward until
    the `status_code=409` closer."""
    import re
    m = re.search(
        r'if _flip_stale\["kind"\]\s*==\s*"dna_in_flight":(.*?)status_code=409',
        src,
        re.DOTALL,
    )
    if not m:
        return ""
    return m.group(1)


def test_dna_in_flight_response_has_answers_applied_flag():
    """Source-level: the dna_in_flight branch must
    include an explicit `answers_applied: False` (or
    equivalent semantic) flag."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    # Find the dna_in_flight return.
    block = _extract_response_block(src)
    assert block, (
        "BRAIN-124 regression: dna_in_flight response "
        "branch should still exist."
    )
    # Look for the explicit answers-not-saved signal.
    # a495 (BRAIN-126): the contract was extracted into
    # `_wizard_conflict_response`. The helper invocation
    # itself satisfies the BRAIN-124 invariant —
    # `answers_applied: false` is now baked into the
    # helper's payload.
    has_flag = (
        "answers_applied" in block
        or "answers_saved" in block
        or "answers_discarded" in block
        or "your_answers" in block
        or "_wizard_conflict_response(" in block
    )
    assert has_flag, (
        "BRAIN-124 regression: dna_in_flight response "
        "must include an explicit `answers_applied: "
        "false` (or equivalent) flag. Without it, "
        "clients can't deterministically distinguish "
        "'wait for active run' from 'your edits "
        "were dropped'."
    )


def test_dna_in_flight_response_surfaces_current_revision():
    """Source-level: the response must include the
    server's current `_wizard_revision` so the client
    can compare to its captured value and decide
    whether to reload."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    block = _extract_response_block(src)
    assert block, "BRAIN-124 regression: response branch missing"
    assert "wizard_revision" in block, (
        "BRAIN-124 regression: dna_in_flight response "
        "must include `wizard_revision` so the client "
        "can reconcile its optimistic-concurrency "
        "baseline. Without it, the client can't tell "
        "if a reload is required."
    )


def test_dna_in_flight_message_warns_answers_not_saved():
    """Source-level: the error string must explicitly
    tell the user their submitted answers were not
    saved. The pre-fix copy ('wait for it to finish')
    incorrectly implied the answers were part of the
    active run."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    block = _extract_response_block(src)
    assert block, "BRAIN-124 regression: response branch missing"
    # The user-facing error string should contain
    # explicit language about discarded answers.
    has_explicit_warning = (
        "not saved" in block.lower()
        or "not applied" in block.lower()
        or "discarded" in block.lower()
        or "lost" in block.lower()
        or "weren't" in block.lower()
        or "did not save" in block.lower()
    )
    assert has_explicit_warning, (
        "BRAIN-124 regression: the dna_in_flight error "
        "message must explicitly state that the user's "
        "submitted answers were not saved. Without that "
        "language, users wrongly believe their edits "
        "are part of the active run."
    )


def test_dna_in_flight_response_includes_epoch():
    """Source-level: the response includes
    `wizard_epoch` so the client can detect a reset
    boundary too (BRAIN-81)."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    block = _extract_response_block(src)
    assert block, "BRAIN-124 regression: response branch missing"
    # a495 (BRAIN-126): contract baked into the shared
    # helper — the helper call counts as proof.
    assert (
        "wizard_epoch" in block
        or "_wizard_conflict_response(" in block
    ), (
        "BRAIN-124 regression: dna_in_flight response "
        "must include `wizard_epoch` for full state "
        "reconciliation."
    )


def test_flip_mutator_captures_current_state_for_response():
    """Source-level: the flip mutator must capture the
    current revision/epoch into the `_flip_stale` dict
    (or equivalent) when it short-circuits on
    dna_in_flight, so the endpoint can pass them
    through to the response.

    Without the capture, the endpoint would have to
    do a separate `db.get_settings` after the merge —
    a wasted round-trip; the mutator already had the
    values in scope.
    """
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    # The flip mutator's `dna_in_flight` branch
    # should set the rev / epoch into the stale dict.
    import re
    # Match the dna_in_flight branch in the mutator.
    m = re.search(
        r'_flip_stale\["kind"\]\s*=\s*"dna_in_flight"(.*?)return cur',
        src,
        re.DOTALL,
    )
    assert m, (
        "BRAIN-124 regression: the flip mutator's "
        "dna_in_flight branch should still exist and "
        "return cur."
    )
    branch = m.group(1)
    has_capture = (
        "_flip_stale" in branch and (
            "wizard_revision" in branch
            or "_wizard_revision" in branch
            or "revision" in branch
        )
    )
    assert has_capture, (
        "BRAIN-124 regression: when the flip mutator "
        "short-circuits on dna_in_flight, it should "
        "capture the current `_wizard_revision` (and "
        "epoch) into `_flip_stale` so the endpoint can "
        "return them to the client without a second "
        "DB round-trip."
    )
