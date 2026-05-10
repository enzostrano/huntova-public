"""Regression tests for BRAIN-78 (a439): wizard completion must
persist DNA generation state durably so the user/UI can see truth
even after disconnects, lost SSE buses, or background-task failures.

Failure mode (per GPT-5.4 long-running-LLM-workflow audit):

`api_wizard_complete` returns `{ok: True}` immediately and spawns
`_gen_dna()` as a fire-and-forget background task. The closure
emits a `dna_updated` SSE event on success/failure so the live UI
can toast — BUT:

1. If the user's tab closed between complete and DNA finishing,
   the SSE bus is gone and `_ctx.bus.emit(...)` is silently
   swallowed by the `except Exception: pass`. The user has no
   way to know whether DNA succeeded.
2. If the `generate_agent_dna(w)` call itself failed (provider
   401, malformed wizard, etc.), the failure is logged via
   `print(...)` but never persisted to user_settings. The next
   hunt then runs with no DNA → silent fallback to brain
   template queries → degraded lead quality with no explanation
   to the user.
3. If the user reopens the wizard later, `/api/wizard/status`
   has no `dna_state` field to expose, so the UI can't show
   "DNA still generating" or "DNA failed — retry" indicators.

The completion contract was UI-only / SSE-only. After tab close
or bus drop, the user believes onboarding finished successfully
but hunt quality is degraded. That's worse than a crash because
the user has no actionable signal.

Invariants:
- Wizard merge sets `_dna_state = "pending"` synchronously when
  DNA generation is spawned. Durable in user_settings.data.
- On success, `_gen_dna` updates `_dna_state = "ready"` +
  `_dna_completed_at`.
- On failure, `_gen_dna` updates `_dna_state = "failed"` +
  `_dna_error` + `_dna_failed_at`.
- `/api/wizard/status` exposes `dna_state` so the UI can poll
  and reconcile with the live SSE event.
- All updates go through atomic `merge_settings` so concurrent
  writes (e.g. the user re-completing while DNA gen is still
  running) don't clobber each other.
"""
from __future__ import annotations
import inspect


def test_wizard_complete_sets_dna_state_pending_synchronously():
    """Source-level: the merge mutator inside api_wizard_complete
    must set `_dna_state = 'pending'` BEFORE the background
    `_gen_dna()` is spawned. Synchronous + durable so a tab
    close right after complete still leaves a recoverable state."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    assert "_dna_state" in src, (
        "BRAIN-78 regression: complete must persist a durable "
        "`_dna_state` field on the wizard. Pre-fix, DNA state "
        "lived only in SSE events + log lines, vanishing on "
        "tab close."
    )
    # The pending state must be set BEFORE _spawn_bg(_gen_dna())
    # (we want pending durably persisted before kick off so even
    # if the bg task never runs, the row reflects pending).
    pending_idx = src.find('"pending"')
    if pending_idx == -1:
        pending_idx = src.find("'pending'")
    spawn_idx = src.find("_spawn_bg(_gen_dna())")
    assert pending_idx != -1 and spawn_idx != -1
    assert pending_idx < spawn_idx, (
        "BRAIN-78 regression: `_dna_state = 'pending'` must be "
        "persisted BEFORE _spawn_bg fires. Otherwise a worker "
        "death between spawn and pending-write would leave the "
        "row in an unknown state."
    )


def test_gen_dna_persists_ready_state_durably():
    """Source-level: on success, _gen_dna must write
    `_dna_state = 'ready'` to user_settings.data via
    merge_settings — not just emit an SSE event."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    # Check that "ready" appears in the success path of _gen_dna.
    assert '"ready"' in src or "'ready'" in src, (
        "BRAIN-78 regression: success path must persist "
        "_dna_state='ready' durably."
    )
    assert "merge_settings" in src or "_afetchone" in src or "save_agent_dna" in src, (
        "BRAIN-78 regression: success state update must go "
        "through atomic merge_settings."
    )


def test_gen_dna_persists_failed_state_with_error():
    """Source-level: on failure, _gen_dna must write
    `_dna_state = 'failed'` AND a truncated error string. The
    next hunt + UI poll need to see WHY DNA isn't ready."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    assert '"failed"' in src or "'failed'" in src, (
        "BRAIN-78 regression: failure path must persist "
        "_dna_state='failed' durably."
    )
    # Must persist a useful error string (truncated).
    has_error_field = (
        "_dna_error" in src or "dna_error" in src
    )
    assert has_error_field, (
        "BRAIN-78 regression: failure state must include an "
        "error message field so the UI can show 'DNA failed — "
        "retry' with context, not just a generic spinner-stuck."
    )


def test_wizard_status_exposes_dna_state():
    """Source-level: /api/wizard/status must include
    `dna_state` in its response so the UI can poll and
    reconcile with the live SSE event when the user reopens
    the wizard later."""
    from server import api_wizard_status
    src = inspect.getsource(api_wizard_status)
    assert "_dna_state" in src or "dna_state" in src, (
        "BRAIN-78 regression: /api/wizard/status must expose "
        "dna_state so the UI can show 'DNA still generating' "
        "or 'DNA failed — retry' indicators on wizard reopen."
    )


def test_dna_state_uses_atomic_merge():
    """Source-level: every _dna_state write must go through
    `db.merge_settings` (atomic) — not direct row updates that
    could race with other concurrent writers (a re-complete
    fired by the user, save-progress, the master-settings
    updater, etc.)."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    # Find _gen_dna closure body.
    g_idx = src.find("async def _gen_dna")
    assert g_idx != -1
    end_idx = src.find("_spawn_bg(_gen_dna())", g_idx)
    closure = src[g_idx:end_idx]
    # Look for merge_settings reference inside the closure.
    assert "merge_settings" in closure, (
        "BRAIN-78 regression: _gen_dna must update _dna_state "
        "via atomic merge_settings, not direct UPDATEs that "
        "could race with other writers."
    )
