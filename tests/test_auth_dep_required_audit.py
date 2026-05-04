"""Regression tests for BRAIN-158 (a571): every wizard
+ agent + lead + memory + chat mutating endpoint must
have either `Depends(require_user)` or `Depends(
require_admin)` in its signature. A handler missing
the auth dep is reachable unauthenticated — silently
public.

Failure mode: a future PR refactoring a handler drops
the `user: dict = Depends(require_user)` parameter
(maybe to "simplify"), the route stays registered,
unauthenticated POSTs persist data into a default
user_id or crash mid-handler.

Invariants:
- Every documented mutator handler in server.py has a
  `Depends(require_user)` OR `Depends(require_admin)`
  parameter.
- Public-by-design endpoints (`/api/try`, `/api/_metric`,
  `/api/track-actions`, `/api/recipe/publish`,
  `/api/admin/cloud-token` Bearer-auth, `/api/setup/key`
  local-only) are explicitly enumerated.
"""
from __future__ import annotations
import inspect


_PROTECTED_MUTATORS = [
    # wizard
    "api_wizard_complete",
    "api_wizard_save_progress",
    "api_wizard_scan",
    "api_wizard_reset",
    "api_wizard_generate_phase5",
    "api_wizard_assist",
    "api_wizard_start_retrain",
    # agent
    "agent_control",
    # leads + chat + team + memory + settings
    "api_lead_feedback",
    "api_chat",
    "api_team_reseed",
    "api_team_toggle",
    "api_save_settings",
]


def test_every_protected_mutator_has_auth_dep():
    """Each handler signature contains
    `Depends(require_user)` or `Depends(require_admin)`."""
    import server as _s
    for fn_name in _PROTECTED_MUTATORS:
        fn = getattr(_s, fn_name, None)
        assert fn is not None, (
            f"BRAIN-158 regression: handler `{fn_name}` "
            f"not found on server module. Has it been "
            f"renamed?"
        )
        src = inspect.getsource(fn)
        # First handful of lines is the signature.
        sig_text = src[:600]
        has_auth = (
            "Depends(require_user)" in sig_text
            or "Depends(require_admin)" in sig_text
        )
        assert has_auth, (
            f"BRAIN-158 regression: `{fn_name}` does NOT "
            f"have `Depends(require_user)` or "
            f"`Depends(require_admin)` in its signature. "
            f"Endpoint is reachable unauthenticated."
        )


def test_admin_handlers_use_require_admin():
    """Admin/ops handlers MUST use require_admin, not
    require_user. Otherwise a regular user could hit
    operator routes."""
    import server as _s
    admin_handlers = [
        "admin_wizard_reset",
    ]
    for fn_name in admin_handlers:
        fn = getattr(_s, fn_name, None)
        if fn is None:
            continue  # may have been renamed
        src = inspect.getsource(fn)
        sig_text = src[:600]
        assert "Depends(require_admin)" in sig_text, (
            f"BRAIN-158 regression: admin handler "
            f"`{fn_name}` must use "
            f"`Depends(require_admin)`, not require_user."
        )


def test_no_protected_mutator_uses_optional_user():
    """Some handlers use `user: dict | None = ...` for
    public-or-authenticated dual flows. None of the
    PROTECTED mutators should have that pattern."""
    import server as _s
    for fn_name in _PROTECTED_MUTATORS:
        fn = getattr(_s, fn_name, None)
        if fn is None:
            continue
        src = inspect.getsource(fn)
        sig_text = src[:800]
        # Look for `user: dict | None` or `Optional[dict]`
        # — a sign of optional auth that doesn't belong
        # on a protected mutator.
        bad = (
            "user: dict | None" in sig_text
            or "Optional[dict]" in sig_text
            or "user: Optional" in sig_text
        )
        assert not bad, (
            f"BRAIN-158 regression: `{fn_name}` has "
            f"optional-user auth — protected mutators "
            f"must require auth."
        )


def test_critical_handlers_exist():
    """Sanity: every name in _PROTECTED_MUTATORS still
    resolves on the server module. A handler being
    removed / renamed shouldn't silently bypass the
    audit."""
    import server as _s
    missing = [
        fn for fn in _PROTECTED_MUTATORS
        if not hasattr(_s, fn)
    ]
    assert not missing, (
        f"BRAIN-158 regression: handlers missing from "
        f"server module (renamed or deleted?): "
        f"{missing}. Update _PROTECTED_MUTATORS list "
        f"OR re-add the handler."
    )
