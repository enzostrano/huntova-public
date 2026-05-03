"""Regression tests for BRAIN-89 (a458): the BRAIN-85 idempotency
fingerprint must never short-circuit when the derived artifacts
it represents (brain, dossier) are missing or empty.

Failure mode (Per Huntova engineering review on idempotency-key
atomicity):

BRAIN-85 stores `_last_complete_fingerprint` inside the same
merge mutator that writes `normalized_hunt_profile` (brain) and
`training_dossier`. Single `merge_settings` call → single SQLite
transaction → atomicity is guaranteed at the DB layer for the
happy path.

But the system can still arrive at "fingerprint present, brain
empty" through paths the atomicity guarantee doesn't cover:

- A future refactor accidentally re-orders the mutator body.
- A future hot-fix migration mutates wizard state without going
  through the same code path (e.g. an admin rollback that
  cleared brain but missed the fingerprint).
- Pre-BRAIN-85 installs upgrade with a stale wizard row that
  has training_dossier but no fingerprint (handled fine).
  BUT pre-BRAIN-85 installs that complete a wizard, then a
  hypothetical future migration writes the fingerprint
  retroactively without re-validating the brain.
- Direct DB tinkering (operator running an UPDATE).

If any of these paths leave fingerprint+artifacts inconsistent,
BRAIN-85's short-circuit returns `reused: true` even though the
agent will then run with no brain — silent quality degradation.

Standard idempotency-record guidance: the dedup marker and the
business result must move as one atomic unit. The READ side of
the cache must defensively verify the result still exists.

Invariants:
- The BRAIN-85 cache hit conditions include
  `normalized_hunt_profile` being a non-empty dict AND
  `training_dossier` being a non-empty dict in the snapshot.
  If either is missing/empty, the short-circuit must NOT fire;
  the full pipeline runs to repair the missing artifacts.
- Defensive: the final merge that writes the fingerprint must
  ALSO refuse to write it if `brain` or `dossier` from the
  compute step is missing or non-dict (e.g. a future
  `_build_hunt_brain` regression returning None silently).
"""
from __future__ import annotations
import inspect


def test_short_circuit_requires_brain_present():
    """Source-level: the BRAIN-85 cache hit condition must
    include a check that `normalized_hunt_profile` exists and
    is non-empty in the snapshot."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    # Find the cache-hit if-block.
    fire_idx = src.find('"reused": True')
    if fire_idx == -1:
        fire_idx = src.find("'reused': True")
    assert fire_idx != -1
    # Look back ~1000 chars for the conditions.
    block = src[max(0, fire_idx - 1500):fire_idx]
    assert "normalized_hunt_profile" in block, (
        "BRAIN-89 regression: BRAIN-85 short-circuit must verify "
        "`normalized_hunt_profile` is present in the snapshot "
        "before returning `reused: true`. Otherwise an "
        "operator-corrupted row (fingerprint without brain) "
        "permanently short-circuits with no actual brain."
    )


def test_short_circuit_requires_dossier_present():
    """Same defense for `training_dossier`."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    fire_idx = src.find('"reused": True')
    if fire_idx == -1:
        fire_idx = src.find("'reused': True")
    assert fire_idx != -1
    block = src[max(0, fire_idx - 1500):fire_idx]
    assert "training_dossier" in block, (
        "BRAIN-89 regression: short-circuit must also verify "
        "`training_dossier` exists before reusing the prior "
        "completion."
    )


def test_short_circuit_falls_through_when_brain_empty():
    """The short-circuit's eligibility check must be tight
    enough that an empty-dict brain doesn't pass. Look for an
    `isinstance(..., dict)` check or an explicit length / "not"
    truthiness gate near the brain reference."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    fire_idx = src.find('"reused": True')
    if fire_idx == -1:
        fire_idx = src.find("'reused': True")
    block = src[max(0, fire_idx - 1500):fire_idx]
    # Either explicit dict-and-non-empty check, or `bool(...)`,
    # or `isinstance(...,dict) and len(...) > 0`-style.
    has_emptiness_guard = (
        "isinstance" in block
        or "and brain" in block
        or "and _prior_brain" in block
    )
    assert has_emptiness_guard, (
        "BRAIN-89 regression: the brain-presence check must "
        "be explicit enough to reject an empty dict, not just "
        "a missing key. An empty `normalized_hunt_profile = {}` "
        "would otherwise pass a naive `'in'` check."
    )


def test_final_merge_refuses_to_write_fingerprint_with_missing_brain():
    """Defense in depth: even if the cache READ check is sound,
    the WRITE side must also refuse to advance the fingerprint
    when `brain` or `dossier` is missing/non-dict from the
    compute step. A regression in `_build_hunt_brain` returning
    None would otherwise persist a fingerprint pointing at a
    null brain."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    # Find the LAST fingerprint reference — that's the assignment
    # site. The first reference is the read-side `_w_snap.get(...)`.
    fp_write_idx = src.rfind('w["_last_complete_fingerprint"]')
    assert fp_write_idx != -1, (
        "BRAIN-89 sanity: fingerprint-assignment line not found."
    )
    # Look back ~1500 chars for the guard that must precede the write.
    block = src[max(0, fp_write_idx - 1500):fp_write_idx + 500]
    # The guard should reference brain or dossier dict-ness.
    has_write_guard = (
        "isinstance(brain, dict)" in block
        or "isinstance(dossier, dict)" in block
        or "brain and dossier" in block
        or "_artifacts_ok" in block
    )
    assert has_write_guard, (
        "BRAIN-89 regression: the fingerprint write must be "
        "guarded by an explicit check that `brain` and `dossier` "
        "from the compute step are non-empty dicts. Otherwise "
        "a silent None from `_build_hunt_brain` lands a "
        "fingerprint pointing at no derived state."
    )
