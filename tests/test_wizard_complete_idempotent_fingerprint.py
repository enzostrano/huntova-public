"""Regression tests for BRAIN-85 (a454): /api/wizard/complete must
short-circuit on a duplicate submit (same input fingerprint, same
epoch, last attempt not failed) instead of re-running brain +
dossier + DNA generation.

Failure mode (Per Huntova engineering review on idempotent expensive
endpoints):

A user clicks Complete training. Brain build + dossier + background
DNA generation all run (multi-second + BYOK spend). User reloads,
hits "Complete training" again before realizing the spinner finished,
or duplicate-submits via a flaky network retry — the SAME profile +
history get reprocessed from scratch. Every duplicate submit:

- Re-runs the synchronous brain+dossier compute (CPU, blocks the
  request for up to 45s via the BRAIN-72 watchdog).
- Re-spawns `_gen_dna()` which makes a real BYOK provider call.
- Bumps `_train_count` and `_knowledge` again, polluting the audit
  trail with redundant entries that all describe the same submit.
- Replaces `_last_trained` with a fresh timestamp even though the
  trained brain hasn't changed.

Standard fix per idempotent-POST guidance: fingerprint the canonical
input, store the fingerprint + epoch on success, and short-circuit
when a subsequent submit matches.

Invariants:
- Wizard state stores `_last_complete_fingerprint` +
  `_last_complete_epoch` after a successful complete.
- `/api/wizard/complete` computes the fingerprint of the validated
  profile + history canonically (sorted keys, deterministic
  serialization) and compares to the stored value.
- Match AND same epoch AND `_dna_state != "failed"` → return early
  with `{ok: True, reused: True}`. No brain build, no dossier, no
  `_spawn_bg(_gen_dna())`, no `_train_count` increment.
- Mismatch (real edit, reset/new epoch, or last-attempt failure) →
  full pipeline runs as before.
- Reset boundary (BRAIN-81 epoch bump) invalidates the cached
  fingerprint automatically since the epoch comparison fails.
"""
from __future__ import annotations
import inspect


def test_complete_endpoint_computes_input_fingerprint():
    """Source-level: api_wizard_complete must compute a fingerprint
    of the canonical input (profile + history). Required to detect
    duplicate submits."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    has_fingerprint = (
        "_complete_fingerprint" in src
        or "_input_fingerprint" in src
        or "_canonical_fingerprint" in src
        or "fingerprint" in src.lower()
    )
    assert has_fingerprint, (
        "BRAIN-85 regression: api_wizard_complete must compute a "
        "fingerprint of (profile, history). Without it, duplicate "
        "submits cannot be detected and every re-submit re-spends "
        "BYOK on brain + dossier + DNA."
    )


def test_complete_uses_canonical_serialization_for_fingerprint():
    """The fingerprint must be canonical (sorted keys) so semantically
    identical payloads with different key order produce the same
    hash. `json.dumps(..., sort_keys=True)` is the standard."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    assert "sort_keys=True" in src, (
        "BRAIN-85 regression: fingerprint must use canonical JSON "
        "serialization (`sort_keys=True`). Otherwise two semantically "
        "identical submits with different key order would compute "
        "different hashes and never short-circuit."
    )


def test_complete_uses_sha256_for_fingerprint():
    """Stable hash, not Python's `hash(...)` (which is randomized
    per process). SHA256 hexdigest is the standard."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    assert "sha256" in src.lower(), (
        "BRAIN-85 regression: fingerprint must use SHA256 (or another "
        "stable cryptographic hash). Python's built-in `hash()` is "
        "randomized per-process and won't survive restarts."
    )


def test_complete_persists_fingerprint_on_success():
    """The merge mutator must persist `_last_complete_fingerprint` +
    `_last_complete_epoch` so the next submit can compare."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    assert "_last_complete_fingerprint" in src, (
        "BRAIN-85 regression: success path must persist "
        "`_last_complete_fingerprint` for future short-circuit checks."
    )
    assert "_last_complete_epoch" in src, (
        "BRAIN-85 regression: success path must persist "
        "`_last_complete_epoch` so a reset invalidates the cached "
        "fingerprint automatically."
    )


def test_complete_short_circuit_returns_reused_marker():
    """When the short-circuit fires, the response must carry a
    distinct marker so the client can show 'Already up to date'
    rather than 'Re-trained'."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    has_marker = (
        '"reused"' in src
        or "'reused'" in src
        or '"idempotent"' in src
        or "'idempotent'" in src
    )
    assert has_marker, (
        "BRAIN-85 regression: idempotent short-circuit response must "
        "carry a distinct marker (e.g. `reused: true` or `idempotent: "
        "true`) so the UI can distinguish from a real retrain."
    )


def test_short_circuit_does_not_run_when_dna_state_is_failed():
    """If the last attempt's DNA failed, a duplicate submit should
    re-run the pipeline (the user is retrying for a reason).
    Otherwise a permanently failed wizard could never recover via
    a re-submit."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    # The short-circuit must check _dna_state != "failed" (or
    # equivalent guard).
    assert ('"failed"' in src or "'failed'" in src), (
        "BRAIN-85 regression: the short-circuit must NOT fire when "
        "the prior DNA generation failed. Otherwise a permanently "
        "failed wizard cannot recover via the same submit."
    )


def test_short_circuit_runs_BEFORE_brain_dossier_compute():
    """Source-level: the fingerprint check must precede the
    `_apply_wizard_mutations(_w_snap)` + brain+dossier compute, so
    the short-circuit actually saves the BYOK spend."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    # Fingerprint comparison must come before the brain build.
    fingerprint_idx = src.find("_last_complete_fingerprint")
    apply_idx = src.find("_apply_wizard_mutations(_w_snap)")
    assert fingerprint_idx != -1 and apply_idx != -1
    assert fingerprint_idx < apply_idx, (
        "BRAIN-85 regression: fingerprint check must run BEFORE "
        "_apply_wizard_mutations + brain build. Otherwise the "
        "short-circuit happens AFTER the expensive work and saves "
        "no spend."
    )
