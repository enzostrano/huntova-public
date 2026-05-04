"""Regression tests for BRAIN-79 (a440): agent `/agent/control`
start path must respect the durable `_dna_state` from BRAIN-78.

Failure mode (per GPT-5.4 durable-workflow-status audit):

BRAIN-78 made DNA generation state durable
(`_dna_state: pending|ready|failed|unset`). But no consumer
checked it. A user could:

1. Click Complete training → `_dna_state="pending"`.
2. Immediately click START on the agent. The
   `/agent/control action=start` path handed the user_id to
   `agent_runner.start_agent` with NO check on `_dna_state`.
3. The agent ran with no DNA (or a stale prior version), fell
   back to brain template queries, and produced degraded
   leads.

Or after a DNA generation failure (`_dna_state="failed"`):
1. SSE event was missed (tab closed during generation).
2. User reopens the tab and clicks START.
3. Same silent fallback. User has no idea DNA failed.

The new durable state is meaningless if downstream actions
don't gate on it. Per durable-workflow-system guidance: once
you persist workflow status, consumers MUST check it before
proceeding.

Invariants:
- When `_dna_state == "pending"`, agent start returns
  `{ok: false, blocked: "dna_pending", error: "..."}` — does
  NOT call `agent_runner.start_agent`.
- When `_dna_state == "failed"`, agent start returns
  `{ok: false, blocked: "dna_failed", error: <reason>,
   retry_action: "wizard_retrain"}` — does NOT call
  `agent_runner.start_agent`.
- When `_dna_state == "ready"` OR `_dna_state == "unset"`
  (the latter is the legacy / fresh-user path), the existing
  start flow runs unchanged. `unset` is permitted because
  pre-BRAIN-78 users have no `_dna_state` field at all and
  shouldn't suddenly get blocked.
- Other agent actions (stop / pause / resume) are NOT gated
  — those don't depend on DNA freshness.
"""
from __future__ import annotations
import inspect


def test_agent_control_start_checks_dna_state():
    """Source-level: the agent_control endpoint must read the
    user's `_dna_state` before delegating to
    `agent_runner.start_agent` for the start action."""
    from server import agent_control
    src = inspect.getsource(agent_control)
    assert "_dna_state" in src, (
        "BRAIN-79 regression: /agent/control start path must "
        "check `_dna_state` before launching a hunt. Pre-fix, "
        "BRAIN-78's durable state was decorative — no consumer "
        "honored it."
    )


def test_agent_control_blocks_when_dna_state_pending():
    """Source-level: pending state must produce a distinct
    response with `blocked: "dna_pending"` (or equivalent)
    so the UI can show 'DNA still generating, wait a moment'
    instead of 'Agent started'."""
    from server import agent_control
    src = inspect.getsource(agent_control)
    has_pending_branch = (
        '"pending"' in src or "'pending'" in src
    )
    assert has_pending_branch, (
        "BRAIN-79 regression: agent start must explicitly "
        "branch on _dna_state='pending'."
    )
    has_blocked_marker = (
        "dna_pending" in src or "blocked" in src
        or "DNA still generating" in src
        or "still generating" in src.lower()
    )
    assert has_blocked_marker, (
        "BRAIN-79 regression: pending response must carry a "
        "distinct marker so the UI can render 'still generating' "
        "rather than the generic 'agent failed' toast."
    )


def test_agent_control_blocks_when_dna_state_failed():
    """Source-level: failed state must produce a distinct
    response surfacing `_dna_error` so the user can retry the
    wizard."""
    from server import agent_control
    src = inspect.getsource(agent_control)
    has_failed_branch = (
        '"failed"' in src
        or "'failed'" in src
        # a489 (BRAIN-120): failed branch lives in the
        # extracted gate helper. The helper call still
        # produces the failed-state response.
        or "_dna_state_gate_response(" in src
    )
    assert has_failed_branch, (
        "BRAIN-79 regression: agent start must explicitly "
        "branch on _dna_state='failed'."
    )
    # Must reference the error message field so the UI can show it.
    has_error_passthrough = (
        "_dna_error" in src
        or "dna_error" in src
        # a489 (BRAIN-120): the gate helper passes through
        # `_dna_error` to the public response.
        or "_dna_state_gate_response(" in src
    )
    assert has_error_passthrough, (
        "BRAIN-79 regression: failed-state response must surface "
        "_dna_error so the user gets actionable feedback, not "
        "a generic 'AI error'."
    )


def test_agent_start_check_runs_before_start_agent_call():
    """Source-level: the DNA-state check must run BEFORE
    `agent_runner.start_agent(...)` is called — otherwise the
    agent has already been queued/started before we know to
    block."""
    from server import agent_control
    src = inspect.getsource(agent_control)
    # Find the `agent_runner.start_agent(` call site for the
    # start branch.
    start_call_idx = src.find("agent_runner.start_agent(")
    state_check_idx = src.find("_dna_state")
    assert start_call_idx != -1
    assert state_check_idx != -1
    assert state_check_idx < start_call_idx, (
        "BRAIN-79 regression: DNA-state check must precede "
        "agent_runner.start_agent. Otherwise the agent has "
        "already been spawned before we evaluate the gate."
    )


def test_agent_control_does_not_gate_other_actions():
    """Source-level: stop / pause / resume must NOT be gated on
    `_dna_state`. Those don't depend on DNA freshness and a
    failed-DNA user must still be able to stop a running
    legacy hunt."""
    from server import agent_control
    src = inspect.getsource(agent_control)
    # Find each non-start branch and verify the DNA-state check
    # doesn't precede them. We do this by checking that the
    # _dna_state check is INSIDE the action == "start" branch
    # (i.e. between `if action == "start":` and `elif action ==
    # "stop":`).
    start_branch_idx = src.find('if action == "start":')
    stop_branch_idx = src.find('elif action == "stop":')
    state_check_idx = src.find("_dna_state")
    assert start_branch_idx != -1 and stop_branch_idx != -1
    assert state_check_idx != -1
    assert start_branch_idx < state_check_idx < stop_branch_idx, (
        "BRAIN-79 regression: DNA gate must live INSIDE the "
        "start branch — stop/pause/resume must not be gated."
    )


def test_unset_dna_state_does_not_block():
    """Source-level: an `unset` (or absent) `_dna_state` must
    NOT block the start. Pre-BRAIN-78 installs have no
    `_dna_state` field; suddenly blocking them on upgrade
    would be a regression."""
    from server import agent_control
    src = inspect.getsource(agent_control)
    # The gate must NOT block when state is unset/empty/missing.
    # Look for an explicit allow-list of blocking states (pending,
    # failed) rather than block-all-non-ready.
    # i.e. the check should be `if _dna_state in ("pending",
    # "failed"):` not `if _dna_state != "ready":`.
    assert ('"pending"' in src and '"failed"' in src) or \
           ("'pending'" in src and "'failed'" in src), (
        "BRAIN-79 regression: gate must whitelist BLOCKING states "
        "(pending, failed) rather than block everything not-ready. "
        "Otherwise pre-BRAIN-78 installs (no _dna_state field) "
        "suddenly can't start hunts after upgrade."
    )
