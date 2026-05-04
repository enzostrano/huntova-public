"""Regression tests for BRAIN-130 (a499):
`/api/wizard/start-retrain` must consult the shared
`_dna_state_gate_response` helper before mutating
wizard state — but ONLY honor the `dna_pending`
block. `failed` / `invalid` states ARE the recovery
case start-retrain exists to handle, so the endpoint
must proceed for those.

Failure mode (Per Huntova engineering review on
shared-precondition consistency):

BRAIN-120 (a489) extracted the dna gate into the
shared helper. BRAIN-121 (a490) extended it to
`agent_control`'s resume action. start-retrain
flips `_interview_complete=False` and clears
`_wizard_phase` to send the user back through the
wizard. It currently never consults the gate.

Failure scenario:
1. User clicks Complete → DNA goes pending with
   started_at = T0 (fresh).
2. In a sibling tab, user clicks Re-train. The
   start-retrain endpoint flips
   `_interview_complete=False` while DNA is still
   in flight from tab 1.
3. Now the row is incoherent: DNA pipeline still
   generating (will eventually write back ready/
   failed via the BRAIN-78 mutators) AND the
   wizard is reopened mid-generation. The
   subsequent BRAIN-78 writeback may land on a
   reset wizard, the new wizard run will collide
   with the in-flight one when the user clicks
   Complete again, etc.

But the gate's blocking semantic for `failed` and
`invalid` doesn't fit start-retrain: those states
say "click Re-train to recover" — and start-retrain
IS the Re-train action. Blocking on `failed` would
trap the user behind the very state they're trying
to recover from.

Per Huntova engineering review: any endpoint that
reopens, rewinds, or reinitializes wizard/brain
state must call the same helper before changing
persisted state. The endpoint can choose which
gate kinds to honor.

Invariants:
- `start-retrain` reads settings, calls
  `_dna_state_gate_response`, and ONLY blocks when
  the response's `blocked` is `"dna_pending"`.
  `failed` / `invalid` pass through (this is the
  recovery path).
- Stale pending (BRAIN-123) is reclaimed by the
  helper itself — start-retrain proceeds.
- The gate call appears BEFORE
  `db.merge_settings(...)`.
"""
from __future__ import annotations
import inspect


def test_start_retrain_consults_dna_gate():
    """Source-level: start-retrain must call the
    shared helper. Without it, start-retrain can race
    against an in-flight DNA generation."""
    from server import api_wizard_start_retrain
    src = inspect.getsource(api_wizard_start_retrain)
    assert "_dna_state_gate_response(" in src, (
        "BRAIN-130 regression: api_wizard_start_retrain "
        "must call `_dna_state_gate_response` before "
        "mutating wizard state. Otherwise it races "
        "against an in-flight DNA generation triggered "
        "from a sibling tab."
    )


def test_start_retrain_gate_call_precedes_merge_settings():
    """Source-level: the gate call appears BEFORE
    `db.merge_settings(...)` so a fresh pending
    DNA generation never gets its underlying wizard
    rewound mid-flight."""
    from server import api_wizard_start_retrain
    src = inspect.getsource(api_wizard_start_retrain)
    gate_idx = src.find("_dna_state_gate_response(")
    merge_idx = src.find("db.merge_settings(")
    assert gate_idx >= 0
    assert merge_idx >= 0
    assert gate_idx < merge_idx, (
        "BRAIN-130 regression: gate must run BEFORE "
        "`db.merge_settings`. Pre-fix the merge ran "
        "unconditionally — even with DNA pending."
    )


def test_start_retrain_only_blocks_on_dna_pending():
    """Source-level: start-retrain must check the
    `blocked` field on the gate response and only
    return early when it's `dna_pending`. `failed`
    and `invalid` ARE the states start-retrain
    exists to recover from — those must proceed."""
    from server import api_wizard_start_retrain
    src = inspect.getsource(api_wizard_start_retrain)
    # Look for an explicit branch that filters on
    # the pending kind.
    has_pending_filter = (
        '"dna_pending"' in src
        or "'dna_pending'" in src
        or "block_kinds" in src
    )
    assert has_pending_filter, (
        "BRAIN-130 regression: start-retrain must filter "
        "the gate response — only block on `dna_pending` "
        "(fresh in-flight). Blocking on `failed`/`invalid` "
        "would trap the user since start-retrain IS the "
        "recovery action for those states."
    )


def test_start_retrain_proceeds_on_failed_state():
    """Source-level: confirm there's a code path that
    explicitly allows failed/invalid to proceed past
    the gate. The simplest implementation pattern:
    `if gate is not None and gate.get('blocked') ==
    'dna_pending': return gate`."""
    from server import api_wizard_start_retrain
    src = inspect.getsource(api_wizard_start_retrain)
    # The handler must NOT short-circuit unconditionally
    # on any non-None gate — it must filter.
    import re
    # Match `if gate ... return gate` patterns and
    # check they include a kind filter.
    has_filtered_return = bool(re.search(
        r'(blocked.*dna_pending|dna_pending.*blocked|block_kinds)',
        src,
    ))
    assert has_filtered_return, (
        "BRAIN-130 regression: start-retrain must return "
        "the gate response ONLY when it's a "
        "`dna_pending` block. failed/invalid responses "
        "must pass through so the user can recover."
    )


def test_start_retrain_reads_settings_before_gate():
    """Source-level: must read settings before calling
    the gate (the helper needs the wizard blob)."""
    from server import api_wizard_start_retrain
    src = inspect.getsource(api_wizard_start_retrain)
    get_idx = src.find("db.get_settings(")
    gate_idx = src.find("_dna_state_gate_response(")
    assert get_idx >= 0, (
        "BRAIN-130 regression: start-retrain must read "
        "settings via db.get_settings."
    )
    assert gate_idx >= 0
    assert get_idx < gate_idx, (
        "BRAIN-130 regression: settings read must come "
        "BEFORE the gate call — the helper consumes "
        "the wizard blob."
    )
