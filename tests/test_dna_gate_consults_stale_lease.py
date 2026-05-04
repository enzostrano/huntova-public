"""Regression tests for BRAIN-123 (a492): the shared
`_dna_state_gate_response` helper must consult
`_dna_pending_is_stale(started_at)` before blocking on
`_dna_state="pending"`. Lease-based systems only work
when every reader interprets expiry the same way.

Failure mode (Per Huntova engineering review on
lease-coherence):

BRAIN-111 (a480) added `_dna_pending_is_stale(
started_at_iso)` and integrated it into the BRAIN-110
flip mutator: when `_dna_state="pending"` AND
`_dna_started_at` is older than
`_DNA_PENDING_STALE_AFTER_SEC` (default 600s), the
flip mutator reclaims the stale lease and proceeds.

BRAIN-120 (a489) extracted the shared
`_dna_state_gate_response(wizard_blob)` helper for
agent_control's start branch. BRAIN-121 (a490)
extended it to the resume branch. Neither uses the
staleness check — they both block unconditionally
when `_dna_state == "pending"`.

Result: split-brain behavior.

1. User clicks Complete → DNA goes "pending" with
   started_at = T0.
2. `_gen_dna()` crashes (asyncio cancel, OOM, deploy
   mid-run). The BRAIN-111 try/finally interrupt-
   writeback should cover most cases, but if even
   that writeback fails (e.g. DB transient), the row
   stays at `pending` with started_at = T0.
3. Time passes. T0 is now > 10 minutes ago.
4. User clicks Re-train → flip mutator says "stale,
   reclaim", lease released, new generation starts.
5. User clicks Start (agent) — but if the new
   generation hasn't completed yet, state is still
   "pending" with a fresh started_at. Fine.
6. ALTERNATIVELY: user never clicks Re-train. They
   just click Start. Gate says "pending, block" —
   even though the lease has been stale for hours.
   The user is told "DNA still generating" forever
   until they think to retrain.

The flip mutator's reclaim semantic and the gate's
blocking semantic disagree about the same persisted
state. One reader says "this is recoverable", the
other says "this is in flight". Per Huntova
engineering review on lease-coherence: any code path
that interprets `_dna_state="pending"` must apply the
same staleness policy.

Invariants:
- `_dna_state_gate_response`, when state is "pending",
  consults `_dna_pending_is_stale(_dna_started_at)`.
  Fresh pending → return blocking dict (current
  behavior preserved). Stale pending → treat as
  reclaimable, return None (allow agent to proceed,
  matching the flip mutator's reclaim semantic).
- The TTL constant is the same `_DNA_PENDING_STALE_AFTER_SEC`
  used by the flip mutator. One source of truth.
"""
from __future__ import annotations
from datetime import datetime, timedelta


def test_gate_blocks_fresh_pending():
    """Sanity: a fresh pending lease still blocks
    (current behavior preserved)."""
    import server as _s
    started_recent = (datetime.now() - timedelta(seconds=5)).isoformat()
    out = _s._dna_state_gate_response({
        "_dna_state": "pending",
        "_dna_started_at": started_recent,
    })
    assert out is not None, (
        "BRAIN-123 regression: fresh pending must still "
        "block. Otherwise the gate becomes useless during "
        "legitimate in-flight generation."
    )
    assert out.get("blocked") == "dna_pending"


def test_gate_allows_stale_pending():
    """Behavioral: a pending lease older than the TTL
    must be treated as reclaimable — gate returns None
    so the agent can proceed (matching the flip
    mutator's reclaim semantic). Otherwise the user is
    trapped behind a dead marker until they retrain."""
    import server as _s
    ttl = _s._DNA_PENDING_STALE_AFTER_SEC
    started_long_ago = (datetime.now() - timedelta(seconds=ttl * 6)).isoformat()
    out = _s._dna_state_gate_response({
        "_dna_state": "pending",
        "_dna_started_at": started_long_ago,
    })
    assert out is None, (
        "BRAIN-123 regression: stale pending lease must "
        "be reclaimable on the gate path the same way it "
        "is on the flip path. Split-brain — one path "
        "reclaiming while the other blocks — leaves users "
        "trapped behind dead markers."
    )


def test_gate_allows_pending_with_missing_started_at():
    """Behavioral: a pending lease with no
    `_dna_started_at` (corruption / legacy / partial
    write) is treated as stale by `_dna_pending_is_stale`
    (fail-open). The gate must follow the same policy
    so a corrupted row doesn't permanently block the
    agent."""
    import server as _s
    out = _s._dna_state_gate_response({"_dna_state": "pending"})
    # fail-open via the staleness helper.
    assert out is None, (
        "BRAIN-123 regression: pending with missing "
        "started_at must be treated as stale (fail-open) "
        "to match `_dna_pending_is_stale`'s policy."
    )


def test_gate_allows_pending_with_unparseable_started_at():
    """Behavioral: corrupted timestamp string. The
    staleness helper treats this as stale; the gate
    must too."""
    import server as _s
    out = _s._dna_state_gate_response({
        "_dna_state": "pending",
        "_dna_started_at": "banana-not-a-timestamp",
    })
    assert out is None, (
        "BRAIN-123 regression: pending with unparseable "
        "started_at must be treated as stale, matching "
        "the flip mutator's recovery semantic."
    )


def test_gate_still_blocks_invalid_and_failed():
    """Sanity: the staleness check applies ONLY to
    pending. invalid + failed remain unconditionally
    blocking (no lease-expiry semantic for those —
    they're terminal states)."""
    import server as _s
    out_invalid = _s._dna_state_gate_response({
        "_dna_state": "banana",
    })
    assert out_invalid is not None
    assert out_invalid.get("blocked") == "dna_invalid_state"
    out_failed = _s._dna_state_gate_response({
        "_dna_state": "failed",
        "_dna_error": "Provider 402",
    })
    assert out_failed is not None
    assert out_failed.get("blocked") == "dna_failed"


def test_gate_uses_same_ttl_constant_as_flip_mutator():
    """Source-level: the gate reads the same
    `_DNA_PENDING_STALE_AFTER_SEC` constant the flip
    mutator uses. One source of truth — operators
    tuning the TTL change one place."""
    import server as _s
    import inspect
    src = inspect.getsource(_s._dna_state_gate_response)
    assert (
        "_dna_pending_is_stale(" in src
        or "_DNA_PENDING_STALE_AFTER_SEC" in src
    ), (
        "BRAIN-123 regression: the gate helper must call "
        "`_dna_pending_is_stale(...)` (or directly "
        "reference the TTL constant) so the staleness "
        "policy stays in sync with the flip mutator. "
        "Split-brain readers are the failure mode."
    )
