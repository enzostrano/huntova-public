"""Regression tests for BRAIN-121 (a490): the
`_dna_state_gate_response` helper extracted in BRAIN-120
must also gate the `resume` action in `agent_control`,
not just `start`. Without this, an agent paused while
DNA was ready can be resumed against pending / failed /
invalid DNA state if a sibling tab corrupted or wiped
the row mid-pause.

Failure mode (Per Huntova engineering review on
shared-precondition consistency):

BRAIN-120 (a489) extracted the dna-state gate into a
shared helper. BRAIN-79 / BRAIN-108 / BRAIN-109 / BRAIN-110
all enforce the gate on the `start` action. The other
agent_control actions skip the gate:

- `stop` / `pause` — abort-class. Correctly skip the
  gate (you must always be able to stop a misbehaving
  agent regardless of DNA state).
- `resume` — RE-ACTIVATES the agent. Same precondition
  class as `start`. Currently does NOT consult the gate.

Failure scenario:
1. User clicks Start → DNA was "ready" → agent runs.
2. User clicks Pause → agent pauses.
3. In a sibling tab, user clicks Re-train → BRAIN-88
   flips `_dna_state` to "pending".
4. Original tab clicks Resume → agent resumes against
   `_dna_state="pending"`. The agent thread keeps
   running with stale `ctx._cached_dna` (loaded at
   start) but the persisted state says "pending".
   Operator dashboard shows contradictory information;
   if the agent thread crashes and restarts, it picks
   up the new pending DNA (which is unfinished),
   producing incoherent hunt results.

Or:
1. User pauses agent.
2. Operator runs an SQL UPDATE that corrupts
   `_dna_state` (or a future regression writes a bad
   value).
3. User clicks Resume → agent re-engages with corrupt
   state, no controlled fail-closed.

Per Huntova engineering review on shared-precondition
consistency: any action that re-activates a billable /
state-mutating path must consult the same gate as the
initial activation. Centralized validation only pays
off when every relevant entry point uses it.

Invariants:
- agent_control's `resume` branch consults
  `_dna_state_gate_response` and returns the blocking
  response when state is invalid / pending / failed.
- The gate call appears BEFORE
  `agent_runner.resume_agent(...)` (precondition before
  side effect — same ordering invariant as BRAIN-120's
  start branch).
- Behavioral parity: a corrupted dna state produces the
  same `dna_invalid_state` blocking response as the
  start branch.
"""
from __future__ import annotations
import inspect


def test_agent_control_resume_uses_dna_state_gate():
    """Source-level: agent_control's resume action calls
    the shared `_dna_state_gate_response` helper."""
    from server import agent_control
    src = inspect.getsource(agent_control)
    # The helper must be invoked at least twice — once
    # for start, once for resume. Pre-fix it appeared
    # only once (in the start branch).
    count = src.count("_dna_state_gate_response(")
    assert count >= 2, (
        "BRAIN-121 regression: agent_control must call "
        "`_dna_state_gate_response` in BOTH the start AND "
        "resume branches. Centralized precondition validation "
        "only pays off when every re-activation entry point "
        "uses it."
    )


def test_resume_branch_gate_precedes_resume_agent_call():
    """Source-level: in the resume branch, the gate
    helper call appears BEFORE
    `agent_runner.resume_agent(...)` so a blocked state
    never reaches the side-effect."""
    from server import agent_control
    src = inspect.getsource(agent_control)
    # Find the resume branch.
    resume_branch_idx = src.find('action == "resume"')
    if resume_branch_idx == -1:
        # alt syntax
        resume_branch_idx = src.find("action == 'resume'")
    assert resume_branch_idx >= 0, (
        "BRAIN-121 regression: agent_control should still "
        "have an `action == 'resume'` branch."
    )
    # Within the resume branch (until the next elif/else
    # boundary or end of function), find the gate call
    # and the resume_agent call.
    resume_block = src[resume_branch_idx:resume_branch_idx + 2000]
    gate_idx = resume_block.find("_dna_state_gate_response(")
    resume_call_idx = resume_block.find("agent_runner.resume_agent(")
    assert gate_idx >= 0, (
        "BRAIN-121 regression: gate call missing inside "
        "resume branch."
    )
    assert resume_call_idx >= 0, (
        "BRAIN-121 regression: resume branch should still "
        "call agent_runner.resume_agent."
    )
    assert gate_idx < resume_call_idx, (
        "BRAIN-121 regression: gate must run BEFORE "
        "agent_runner.resume_agent. Otherwise a blocked "
        "DNA state has already re-activated the agent."
    )


def test_helper_invocation_pattern_matches_start_branch():
    """Source-level: the resume-branch gate invocation
    follows the same pattern as the start-branch one
    (consistency = predictable behavior + easier audit).
    Specifically: read settings → extract wizard blob →
    call helper → return blocking response if non-None."""
    from server import agent_control
    src = inspect.getsource(agent_control)
    # Both branches must read settings via db.get_settings.
    assert src.count("await db.get_settings(") >= 2, (
        "BRAIN-121 regression: resume branch must read "
        "settings via db.get_settings (same pattern as "
        "start) so the gate operates on the live row."
    )


def test_stop_pause_remain_ungated():
    """Sanity: stop and pause are abort-class actions —
    they MUST work regardless of DNA state. The gate
    must NOT be applied to them. (A user with corrupted
    DNA must still be able to stop / pause.)"""
    from server import agent_control
    src = inspect.getsource(agent_control)
    # Find the stop branch.
    import re
    # Match the stop branch up to the next elif/else.
    stop_match = re.search(
        r'elif action == "stop":(.*?)elif',
        src,
        re.DOTALL,
    )
    if stop_match:
        stop_body = stop_match.group(1)
        assert "_dna_state_gate_response(" not in stop_body, (
            "BRAIN-121 regression: stop must NOT consult "
            "the dna gate. A user with corrupted DNA must "
            "be able to stop the agent."
        )
    pause_match = re.search(
        r'elif action == "pause":(.*?)elif',
        src,
        re.DOTALL,
    )
    if pause_match:
        pause_body = pause_match.group(1)
        assert "_dna_state_gate_response(" not in pause_body, (
            "BRAIN-121 regression: pause must NOT consult "
            "the dna gate."
        )
