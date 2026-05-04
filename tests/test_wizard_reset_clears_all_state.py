"""Regression tests for BRAIN-80 (a441): wizard reset must clear
ALL derived artifacts + workflow status fields, not just the
form-state subset.

Failure mode (per GPT-5.4 durable-workflow-reset audit):

Pre-fix, the wizard had no user-facing server-side reset. The
`brainReset` button in the UI only cleared `_brainState`
locally — server-side fields persisted forever:

- `_wizard_answers`, `_wizard_phase`, `_wizard_confidence`,
  `_wizard_revision`
- Derived artifacts: `normalized_hunt_profile`,
  `training_dossier`, `archetype`, `archetype_confidence`,
  `scoring_rules`, `_knowledge`, `_train_count`, `_last_trained`
- DNA workflow state (BRAIN-78): `_dna_state`, `_dna_started_at`,
  `_dna_completed_at`, `_dna_error`, `_dna_failed_at`,
  `_dna_version`, `_dna_query_count`

A user who wanted a clean restart got a "fake fresh start": the
form said empty but the server still believed training was
complete, DNA was ready, scoring rules from the prior business
were active. The agent's start path (BRAIN-79) would happily
launch with stale `_dna_state="ready"` from a different ICP.

Per durable-workflow guidance: reset should create a clean new
run, not reuse leftover derived outputs. Once you persist
workflow status (BRAIN-78), reset semantics must be equally
durable + complete.

Invariants:
- `/api/wizard/reset` endpoint exists, requires auth.
- Clears ALL wizard state (full `s["wizard"] = {}` is the
  canonical implementation, matching the admin reset).
- Goes through atomic `merge_settings` so a concurrent agent
  thread / save-progress / DNA generation closure can't race
  in stale writes.
- Returns `{ok: true, reset: true}` so the client can confirm.
- After reset, `/api/wizard/status` shows `dna_state="unset"`
  (not "ready"), `complete=false`, `has_answers=false`.
- `/agent/control action=start` no longer blocks on stale DNA
  state because the field is gone.
"""
from __future__ import annotations
import inspect


def test_wizard_reset_endpoint_exists():
    """The user-facing reset endpoint must exist as
    `/api/wizard/reset` (POST). The admin route at
    `/api/ops/users/{id}/wizard/reset` is for ops, not end
    users."""
    import server as _s
    # Look for a callable matching a likely name.
    candidates = ("api_wizard_reset", "wizard_reset",
                  "api_brain_reset", "api_reset_wizard")
    found = None
    for name in candidates:
        fn = getattr(_s, name, None)
        if fn is not None and callable(fn):
            found = fn
            break
    assert found is not None, (
        "BRAIN-80 regression: a user-facing /api/wizard/reset "
        "endpoint must exist. Pre-fix, the brainReset button "
        "was a local-only form clear; server state persisted, "
        "creating a 'fake fresh start' where DNA / brain / "
        "scoring rules from the prior business stayed active."
    )


def test_wizard_reset_uses_atomic_merge():
    """Source-level: the reset must go through atomic
    `merge_settings`. A direct `save_settings` after
    `get_settings` would race with concurrent writers (agent
    thread bumping _last_trained, save-progress, DNA gen
    closure)."""
    import server as _s
    fn = (
        getattr(_s, "api_wizard_reset", None)
        or getattr(_s, "wizard_reset", None)
        or getattr(_s, "api_brain_reset", None)
        or getattr(_s, "api_reset_wizard", None)
    )
    assert fn is not None
    src = inspect.getsource(fn)
    assert "merge_settings" in src, (
        "BRAIN-80 regression: reset must use db.merge_settings "
        "(atomic) — not get/save which races with concurrent "
        "writers and could round-trip stale state back."
    )


def test_wizard_reset_clears_derived_artifacts():
    """Source-level: the reset mutator must clear the derived
    fields explicitly. Either `s["wizard"] = {}` (full wipe) or
    a per-field clear list that includes all derived names."""
    import server as _s
    fn = (
        getattr(_s, "api_wizard_reset", None)
        or getattr(_s, "wizard_reset", None)
        or getattr(_s, "api_brain_reset", None)
        or getattr(_s, "api_reset_wizard", None)
    )
    src = inspect.getsource(fn)
    # Either full-wipe approach (cleanest) or explicit clear of
    # the named derived fields.
    full_wipe = (
        '"wizard"' in src and ("= {}" in src or "= dict()" in src)
    )
    explicit_clear = all(
        f in src for f in [
            "normalized_hunt_profile",
            "training_dossier",
            "_knowledge",
        ]
    )
    assert full_wipe or explicit_clear, (
        "BRAIN-80 regression: reset must clear derived "
        "artifacts (normalized_hunt_profile, training_dossier, "
        "_knowledge, etc.). Either full wizard wipe or "
        "explicit per-field clear."
    )


def test_wizard_reset_clears_dna_workflow_state():
    """Source-level: BRAIN-78 introduced `_dna_state` /
    `_dna_completed_at` / etc. Those must be cleared by reset
    — otherwise the BRAIN-79 agent gate sees stale "ready"
    from a prior business and skips the block."""
    import server as _s
    fn = (
        getattr(_s, "api_wizard_reset", None)
        or getattr(_s, "wizard_reset", None)
        or getattr(_s, "api_brain_reset", None)
        or getattr(_s, "api_reset_wizard", None)
    )
    src = inspect.getsource(fn)
    full_wipe = (
        '"wizard"' in src and ("= {}" in src or "= dict()" in src)
    )
    explicit_dna_clear = "_dna_state" in src
    assert full_wipe or explicit_dna_clear, (
        "BRAIN-80 regression: reset must clear _dna_state. "
        "After reset, `/api/wizard/status` should show "
        "dna_state='unset' and `/agent/control start` should "
        "not be silently gated on stale 'ready' state."
    )


def test_wizard_reset_requires_authentication():
    """Source-level: must use `Depends(require_user)` — anyone
    being able to anonymously POST a reset would be a trivial
    griefing attack."""
    import server as _s
    fn = (
        getattr(_s, "api_wizard_reset", None)
        or getattr(_s, "wizard_reset", None)
        or getattr(_s, "api_brain_reset", None)
        or getattr(_s, "api_reset_wizard", None)
    )
    src = inspect.getsource(fn)
    assert "require_user" in src, (
        "BRAIN-80 regression: reset endpoint must require "
        "authentication. Otherwise an anonymous attacker can "
        "wipe any user's wizard state."
    )
