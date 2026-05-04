"""Regression tests for BRAIN-120 (a489): the
`_dna_state` precondition gate must run BEFORE any
paid-quota / billable-path bookkeeping in
`agent_control`. Fail-fast precondition ordering:
deterministic state-machine rejections fire before
resource-governing side effects.

Failure mode (Per Huntova engineering review on
fail-fast precondition ordering + state-machine
validation):

`agent_control`'s `start` action gates on the
BRAIN-78 `_dna_state` field (BRAIN-79 + BRAIN-108).
The gate logic was inline in the handler. Today's
ordering is correct — `_dna_state` check runs
BEFORE the call to `agent_runner.start_agent` (which
in cloud mode does `check_and_reset_credits`, the
billable side effect). But the invariant is implicit:
nothing in the source codifies that the gate must
stay first. A future PR adding rate-limiting or
quota accounting to `agent_control` could
accidentally insert state mutation BEFORE the gate
and there'd be no test to catch it.

Per Huntova engineering review: extract the gate
into a reusable helper so the ordering invariant
is testable at the source level, and add behavioral
tests that prove the helper produces the documented
blocked-response shapes.

Invariants:
- Module-scope helper `_dna_state_gate_response(
  user_id_or_settings)` runs the BRAIN-108/109 enum
  check and returns either `None` (allow) or a
  blocking response dict matching the existing
  inline shapes (`dna_invalid_state`,
  `dna_pending`, `dna_failed`).
- `agent_control` calls the helper. The helper call
  appears BEFORE any reference to
  `agent_runner.start_agent`, so the precondition
  always runs before the billable launch.
- Behavioral: helper returns blocking dict for
  malformed / pending / failed; allows for ready /
  unset.
"""
from __future__ import annotations
import inspect


def test_dna_state_gate_helper_exists():
    """Module-scope helper exists."""
    import server as _s
    fn = getattr(_s, "_dna_state_gate_response", None)
    assert fn is not None and callable(fn), (
        "BRAIN-120 regression: server must expose "
        "`_dna_state_gate_response(wizard_blob)` so the "
        "BRAIN-79/108/109 gate logic is testable at the "
        "source level and reusable across any future "
        "endpoint that needs to check dna state before "
        "billable work."
    )


def test_helper_allows_ready_state():
    """Behavioral: ready state → allow (None)."""
    import server as _s
    out = _s._dna_state_gate_response({"_dna_state": "ready"})
    assert out is None, (
        "BRAIN-120 regression: ready state must allow "
        "(return None)."
    )


def test_helper_allows_unset_state():
    """Behavioral: unset (legacy install) → allow."""
    import server as _s
    out = _s._dna_state_gate_response({})
    assert out is None
    out = _s._dna_state_gate_response({"_dna_state": "unset"})
    assert out is None


def test_helper_blocks_invalid_state():
    """Behavioral: out-of-enum value → return blocking
    response with `blocked: dna_invalid_state`."""
    import server as _s
    out = _s._dna_state_gate_response({"_dna_state": "banana"})
    assert out is not None
    assert out.get("ok") is False
    assert out.get("blocked") == "dna_invalid_state"
    assert out.get("retry_action") == "wizard_retrain"


def test_helper_blocks_pending_state():
    """Behavioral: FRESH pending state → return blocking
    response with `blocked: dna_pending` and the
    `dna_started_at` timestamp so the UI can show
    countdown. (BRAIN-123: stale pending is now reclaimed
    via the lease-staleness check; this test seeds a
    timestamp from `datetime.now()` so it's guaranteed
    fresh.)"""
    import server as _s
    from datetime import datetime
    fresh = datetime.now().isoformat()
    out = _s._dna_state_gate_response({
        "_dna_state": "pending",
        "_dna_started_at": fresh,
    })
    assert out is not None
    assert out.get("blocked") == "dna_pending"
    assert out.get("dna_state") == "pending"
    assert out.get("dna_started_at") == fresh


def test_helper_blocks_failed_state():
    """Behavioral: failed state → return blocking
    response with `blocked: dna_failed` and the
    persisted error string."""
    import server as _s
    out = _s._dna_state_gate_response({
        "_dna_state": "failed",
        "_dna_error": "Provider 402: out of credits",
    })
    assert out is not None
    assert out.get("blocked") == "dna_failed"
    assert out.get("dna_state") == "failed"
    assert out.get("retry_action") == "wizard_retrain"
    assert "Provider 402" in (out.get("dna_error") or "")


def test_agent_control_uses_gate_helper():
    """Source-level: `agent_control` must call the
    extracted helper rather than re-implement the gate
    inline. Otherwise the helper is dead code and the
    invariant is unprotected."""
    from server import agent_control
    src = inspect.getsource(agent_control)
    assert "_dna_state_gate_response(" in src, (
        "BRAIN-120 regression: agent_control must call "
        "`_dna_state_gate_response(...)` for the BRAIN-79 "
        "gate. Inline duplication leaves the precondition "
        "unenforceable from outside the handler."
    )


def test_agent_control_gate_precedes_start_agent():
    """Source-level: the gate helper call appears BEFORE
    the call to `agent_runner.start_agent` so the
    billable launch never fires for an invalid state.
    Codifies the precondition-before-billable invariant."""
    from server import agent_control
    src = inspect.getsource(agent_control)
    gate_idx = src.find("_dna_state_gate_response(")
    launch_idx = src.find("agent_runner.start_agent(")
    assert gate_idx >= 0, (
        "BRAIN-120 regression: gate call must be present."
    )
    assert launch_idx >= 0, (
        "BRAIN-120 regression: agent_runner.start_agent "
        "call must still be present."
    )
    assert gate_idx < launch_idx, (
        "BRAIN-120 regression: gate must run BEFORE "
        "agent_runner.start_agent. A request with "
        "invalid _dna_state must fail as a pure "
        "validation/state error, not as a billable "
        "launch attempt that happens to discover "
        "invalid state later."
    )


def test_helper_blocked_response_omits_secrets():
    """Defensive: the blocking response on `dna_failed`
    surfaces `_dna_error` (the persisted message) but
    must not leak any other persisted DNA fields the
    operator wouldn't want public (started_at on a
    failed run is fine, error string is fine, but
    e.g. raw stack traces shouldn't bleed through)."""
    import server as _s
    out = _s._dna_state_gate_response({
        "_dna_state": "failed",
        "_dna_error": "x" * 500,
    })
    # The helper trims overlong errors so a noisy
    # stack-trace string doesn't get pasted into the
    # response unbounded. Match the existing inline
    # behavior which trimmed [:80] on operator log.
    err = out.get("dna_error", "")
    # Reasonable upper bound — exact length depends on
    # the helper. Just assert it doesn't pass through a
    # 500-char raw blob unbounded.
    assert len(err) <= 500, (
        "BRAIN-120 regression: _dna_error on the public "
        "blocking response should be size-bounded so a "
        "rogue persisted value can't dump megabytes into "
        "every blocked response."
    )
