"""Regression tests for BRAIN-95 (a464): the admin wizard reset
endpoint must atomically bump `_wizard_epoch` and use
`merge_settings`, parity with the user-facing
`/api/wizard/reset` (BRAIN-80 + BRAIN-81).

Failure mode (Per Huntova engineering review on reset
generation parity):

The user-facing `/api/wizard/reset` (BRAIN-80, a441) full-wipes
the wizard via `merge_settings` AND carries the old
`_wizard_epoch` forward + bumps it (BRAIN-81, a447). That
guarantees:

- Atomic write — concurrent agent thread / save-progress
  writers can't race in stale state.
- Stale-tab convergence — any tab loaded pre-reset that sends
  a save-progress with `expected_epoch=E_old` gets HTTP 410
  with `error_kind: "wizard_reset"` and reloads itself.

The admin reset (`/api/ops/users/{id}/wizard/reset`, line
11759) had a parallel implementation that:

1. Used `db.save_settings(...)` — non-atomic, races with the
   agent thread + save-progress + DNA gen closure. Same bug
   class as BRAIN-6 (a347) called out for the user merge
   helpers; just on a different code path.
2. Did NOT bump `_wizard_epoch`. So a stale tab open during
   the admin reset would keep its `expected_epoch=E_old`,
   match the (still-E_old) server epoch, and resurrect
   pre-reset answers into the freshly-cleared wizard — the
   exact scenario BRAIN-81 fixed for user reset.

Optimistic-concurrency invariant: every operation that
semantically destroys wizard state must advance the same
generation token. Otherwise stale clients survive resets.

Invariants:
- `admin_wizard_reset` calls `db.merge_settings`, NOT
  `db.save_settings`.
- The mutator preserves + increments `_wizard_epoch` exactly
  like the user reset's `_reset_mutator` from BRAIN-81.
- Post-reset `/api/wizard/status` returns
  `wizard_epoch = E_old + 1`.
- Stale-tab save-progress with `expected_epoch=E_old`
  rejects with HTTP 410 + `error_kind: "wizard_reset"`.
- Audit-log entry still fires (admin action accountability).
"""
from __future__ import annotations
import inspect


def test_admin_reset_uses_atomic_merge_settings():
    """Source-level: admin endpoint must use `db.merge_settings`,
    not `db.save_settings`. The latter is not atomic against
    concurrent writers."""
    from server import admin_wizard_reset
    src = inspect.getsource(admin_wizard_reset)
    assert "merge_settings" in src, (
        "BRAIN-95 regression: admin reset must use "
        "db.merge_settings for atomic writes — concurrent "
        "agent thread / save-progress / DNA closure can race "
        "in stale state otherwise."
    )
    # Strip the docstring (which mentions the pre-fix
    # behavior) before checking — we only care about live
    # callsites, not historical context.
    import re as _re
    code_only = _re.sub(r'""".*?"""', "", src, flags=_re.DOTALL)
    code_only = _re.sub(r"#[^\n]*", "", code_only)
    assert "db.save_settings(" not in code_only, (
        "BRAIN-95 regression: admin reset must NOT call "
        "db.save_settings(...) — that's the non-atomic path "
        "BRAIN-6 (a347) migrated everything else away from."
    )


def test_admin_reset_bumps_wizard_epoch():
    """Source-level: admin reset must increment
    `_wizard_epoch` so stale tabs detect the reset boundary —
    parity with BRAIN-81 user reset."""
    from server import admin_wizard_reset
    src = inspect.getsource(admin_wizard_reset)
    assert "_wizard_epoch" in src, (
        "BRAIN-95 regression: admin reset must bump "
        "`_wizard_epoch` so stale clients see a new generation "
        "token. Parity with /api/wizard/reset (BRAIN-81)."
    )
    # Must read prior epoch + add 1.
    has_carry_pattern = (
        "prior_epoch + 1" in src
        or "_prior_epoch + 1" in src
        or "_prior + 1" in src
    )
    assert has_carry_pattern, (
        "BRAIN-95 regression: admin reset must read prior "
        "epoch + bump (not start fresh at 0). Otherwise an "
        "admin reset right after a user reset could regress "
        "the epoch and a really stale tab would suddenly "
        "match again."
    )


def test_admin_reset_preserves_full_wipe_semantics():
    """Source-level: admin reset must still clear the wizard
    sub-object — same wipe semantics as BRAIN-80 user reset."""
    from server import admin_wizard_reset
    src = inspect.getsource(admin_wizard_reset)
    has_wipe = (
        '"wizard" = {' in src.replace(" ", "")
        or '"wizard"]={' in src.replace(" ", "")
        or '["wizard"]' in src
    )
    assert has_wipe, (
        "BRAIN-95 regression: admin reset must wipe the "
        "wizard sub-object — that's the whole point of the "
        "endpoint."
    )


def test_admin_reset_audit_log_preserved():
    """Don't regress: the existing log_admin_action call
    must still fire so admin accountability is preserved."""
    from server import admin_wizard_reset
    src = inspect.getsource(admin_wizard_reset)
    assert "log_admin_action" in src, (
        "BRAIN-95 regression: admin reset must still log to "
        "log_admin_action. Operator accountability is a "
        "separate concern from the wizard state mutation but "
        "must not be silently dropped during the refactor."
    )


def test_admin_reset_audit_log_includes_reason():
    """Don't regress the existing reason-capture: the audit
    payload must still include the operator's `reason`."""
    from server import admin_wizard_reset
    src = inspect.getsource(admin_wizard_reset)
    assert '"reason": reason' in src or "'reason': reason" in src, (
        "BRAIN-95 regression: admin reset audit log must still "
        "carry the operator's `reason` string."
    )
