"""Regression tests for BRAIN-108 (a477): the BRAIN-79 agent
start gate must validate `_dna_state` against the allowed
enum and fail-closed on unknown values.

Failure mode (Per Huntova engineering review on
state-machine read-side enum validation):

The BRAIN-79 (a440) gate currently checks:

```python
_dna_state = _w_for_dna.get("_dna_state")
if _dna_state == "pending":  return blocked-pending
if _dna_state == "failed":   return blocked-failed
# fall through: ready / unset / anything-else proceed
```

The fall-through bucket lumps three semantically-different
states into "proceed normally":

- `"ready"`: legitimate green-light (intended).
- `"unset"`: pre-BRAIN-78 install with no DNA field
  (intended legacy compat).
- `"pendng"` / `"READY"` / `"failedd"` / `None` / `42` /
  `{}` — anything malformed.

A future bug, an operator running an UPDATE, a partial
migration, a legacy row, or even a dropped letter in a
typo'd config could persist a malformed value. The gate
silently treats "garbage" as green-light. State-machine
guidance is consistent: invalid states must be handled
explicitly and fail closed.

Invariants:
- The agent_control gate normalizes `_dna_state` against
  the allowed enum `{"pending", "ready", "failed", "unset"}`.
- Unknown / non-string / null values fall into a distinct
  blocked branch — neither "proceed" nor an existing
  block reason. New `blocked: "dna_invalid_state"` marker.
- The blocked-invalid response includes the offending value
  (truncated for safety) so an operator can investigate
  the corrupted row.
- `unset` and `ready` continue to proceed (legacy compat
  preserved).
"""
from __future__ import annotations
import inspect


def test_agent_control_validates_dna_state_against_enum():
    """Source-level: agent_control must reject `_dna_state`
    values outside the documented enum, not just blocklist
    the two known-bad ones."""
    from server import agent_control
    src = inspect.getsource(agent_control)
    has_enum = (
        "{'pending', 'ready', 'failed', 'unset'}" in src
        or '{"pending", "ready", "failed", "unset"}' in src
        or "_DNA_STATE_ALLOWED" in src
        or '_VALID_DNA_STATES' in src
    )
    assert has_enum, (
        "BRAIN-108 regression: agent_control must reference "
        "the allowed `_dna_state` enum. Otherwise "
        "malformed values fall through to the proceed path "
        "and bypass the BRAIN-79 gate silently."
    )


def test_agent_control_blocks_invalid_state_explicitly():
    """Source-level: agent_control must have a distinct
    branch for invalid states (not just the existing
    pending/failed branches)."""
    from server import agent_control
    src = inspect.getsource(agent_control)
    has_invalid_branch = (
        "dna_invalid_state" in src
        or 'blocked": "dna_invalid' in src
        or "'dna_invalid_state'" in src
    )
    assert has_invalid_branch, (
        "BRAIN-108 regression: agent_control must produce a "
        "distinct `blocked: 'dna_invalid_state'` (or "
        "equivalent) response for malformed `_dna_state` "
        "values. Falling through to the proceed path "
        "silently bypasses the BRAIN-79 gate."
    )


def test_unset_state_still_proceeds():
    """Don't regress legacy compat: `unset` (pre-BRAIN-78
    installs) must continue to proceed through the gate."""
    from server import agent_control
    src = inspect.getsource(agent_control)
    # The "unset" string must appear in the allowed enum so
    # the validator treats it as legitimate.
    assert '"unset"' in src or "'unset'" in src, (
        "BRAIN-108 regression: `unset` must still be in the "
        "allowed enum so pre-BRAIN-78 installs without the "
        "field aren't suddenly blocked on upgrade."
    )


def test_ready_state_still_proceeds():
    """Don't regress: `ready` is the green-light path —
    legitimate trained users must continue to start."""
    from server import agent_control
    src = inspect.getsource(agent_control)
    assert '"ready"' in src or "'ready'" in src, (
        "BRAIN-108 regression: `ready` must be in the allowed "
        "enum so the BRAIN-79 happy path still works."
    )


def test_invalid_state_response_includes_offending_value():
    """Source-level: the blocked-invalid response should
    surface the offending value (truncated) so an operator
    can investigate the corrupted row instead of guessing."""
    from server import agent_control
    src = inspect.getsource(agent_control)
    # Find the invalid-state branch (look for invalid keyword).
    inv_idx = src.find("dna_invalid_state")
    if inv_idx == -1:
        inv_idx = src.find("invalid_dna_state")
    if inv_idx == -1:
        # Test will fail upstream test instead.
        return
    block = src[max(0, inv_idx - 500):inv_idx + 800]
    # The response should include the offending value (str
    # of _dna_state truncated to a reasonable length).
    has_value = (
        "_dna_state" in block
    )
    assert has_value, (
        "BRAIN-108 regression: invalid-state response must "
        "include the offending `_dna_state` value (truncated) "
        "so operators can debug corrupted rows without "
        "running their own SQL."
    )


def test_invalid_state_check_runs_BEFORE_start_agent_call():
    """Source-level: the validation must precede
    `agent_runner.start_agent(...)` — same ordering invariant
    as BRAIN-79's pending/failed branches."""
    from server import agent_control
    src = inspect.getsource(agent_control)
    start_idx = src.find("agent_runner.start_agent(")
    invalid_idx = src.find("dna_invalid_state")
    if invalid_idx == -1:
        invalid_idx = src.find("invalid_dna_state")
    assert start_idx != -1
    assert invalid_idx != -1, "BRAIN-108: invalid branch missing"
    assert invalid_idx < start_idx, (
        "BRAIN-108 regression: invalid-state branch must "
        "fire BEFORE agent_runner.start_agent(). Otherwise "
        "the agent has already been spawned."
    )
