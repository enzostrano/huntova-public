"""Regression tests for BRAIN-90 (a459): phase-5 dynamic question
schema must persist server-side alongside the answers, so reload
doesn't orphan p5_* values.

Failure mode (Per Huntova engineering review on dynamic-form
state-loss):

`/api/wizard/generate-phase5` builds 5 dynamic questions
client-side. The wizard JS pushes them into the in-memory
`_BRAIN_QUESTIONS` array. The user answers them; save-progress
writes the answers under `p5_*` keys to `_wizard_answers`.

On reload:
- `_BRAIN_QUESTIONS` resets to the 9 base questions.
- Server returns `_wizard_answers` containing `p5_1`, `p5_2`, …
- The wizard renders 9 base questions, NEVER re-fetches
  generate-phase5 (the AI work cost real money + can't be
  reused if the AI returns different questions).
- The p5_* answers exist but the QUESTION TEXT is gone. Brain
  build, dossier generation, and assist context all interpolate
  the answer values without their associated prompts —
  ambiguous semantics, and the UI can't even show the user what
  they answered.

Standard fix per dynamic-form-state-persistence guidance: the
schema and the answers must travel together. Whenever phase-5
runs, persist the cleaned question array alongside the wizard
state with epoch + revision binding.

Invariants:
- `/api/wizard/generate-phase5` writes the cleaned question
  array to `_phase5_questions` via `merge_settings` (atomic).
- `/api/wizard/status` exposes `phase5_questions` so the
  client can rebuild `_BRAIN_QUESTIONS` on reload.
- A wizard reset (BRAIN-80) clears phase-5 along with
  everything else (full wipe semantics already cover this).
- Storage is bounded — at most the 5 cleaned items survive.
"""
from __future__ import annotations
import inspect


def test_generate_phase5_persists_questions_to_wizard_state():
    """Source-level: generate-phase5 must call merge_settings to
    persist the cleaned question array."""
    from server import api_wizard_generate_phase5
    src = inspect.getsource(api_wizard_generate_phase5)
    assert "merge_settings" in src, (
        "BRAIN-90 regression: generate-phase5 must persist the "
        "cleaned question array via merge_settings. Without "
        "this, p5_* answers orphan on reload."
    )


def test_generate_phase5_persists_under_phase5_questions_key():
    """Source-level: the persisted key name must be stable so
    the status endpoint + client load path can find it."""
    from server import api_wizard_generate_phase5
    src = inspect.getsource(api_wizard_generate_phase5)
    assert "_phase5_questions" in src, (
        "BRAIN-90 regression: phase-5 schema must be persisted "
        "under `_phase5_questions` for the status endpoint + "
        "client to consume."
    )


def test_status_endpoint_exposes_phase5_questions():
    """Source-level: /api/wizard/status must expose
    `phase5_questions` so the client can rebuild
    `_BRAIN_QUESTIONS` after reload."""
    from server import api_wizard_status
    src = inspect.getsource(api_wizard_status)
    assert "phase5_questions" in src or "_phase5_questions" in src, (
        "BRAIN-90 regression: /api/wizard/status must expose "
        "the persisted phase-5 question schema. Otherwise the "
        "client never knows the questions exist."
    )


def test_persisted_questions_carry_full_schema():
    """Source-level: each persisted question must carry the
    full schema (question, type, options, placeholder, prefill)
    so the client can faithfully re-render. The cleaning loop
    already builds these fields; the persistence step must
    write them through, not summarize."""
    from server import api_wizard_generate_phase5
    src = inspect.getsource(api_wizard_generate_phase5)
    # The cleaned items have keys: question, type, options,
    # placeholder, prefill. The persist step must write the
    # cleaned array directly, not strip fields.
    assert 'cleaned' in src, (
        "BRAIN-90 sanity: cleaned variable name not found."
    )
    # The persist call must reference the cleaned variable, not
    # a re-summarized version.
    persist_block_idx = src.find("_phase5_questions")
    assert persist_block_idx != -1
    block = src[max(0, persist_block_idx - 600):persist_block_idx + 400]
    assert "cleaned" in block, (
        "BRAIN-90 regression: phase-5 persist must reference "
        "the `cleaned` array directly (full schema). Stripping "
        "fields would break client re-render on reload."
    )


def test_client_load_rehydrates_phase5_questions():
    """The wizard JS load path must, when phase5_questions are
    present on /api/settings (or /api/wizard/status), push them
    into `_BRAIN_QUESTIONS` so they render on reload."""
    with open("templates/jarvis.html", "r", encoding="utf-8") as fh:
        src = fh.read()
    # The client must read `w._phase5_questions` (the storage
    # key on the wizard blob) and push them into the questions
    # array. Look for the rehydration logic anywhere.
    assert "_phase5_questions" in src, (
        "BRAIN-90 regression: client load must read "
        "`w._phase5_questions` and rehydrate `_BRAIN_QUESTIONS`. "
        "Otherwise the persisted schema sits unused and the "
        "answers still orphan."
    )
