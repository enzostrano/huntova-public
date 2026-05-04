"""Regression tests for BRAIN-133 (a502): the destructive
Origin-gated set established by BRAIN-114 (a483) was
incomplete. It covered `/api/wizard/reset` and
`/api/wizard/start-retrain` but missed two other
destructive write paths that warrant the same defense-
in-depth Origin gate:

- `/api/wizard/complete` — on retrain (when the user has
  already completed the wizard once), this overwrites
  the prior brain + dossier in user_settings. A
  successful CSRF bypass on this endpoint silently
  destroys the user's tuned brain. Destructive.
- `/api/ops/users/{user_id}/wizard/reset` — the admin
  operator escape hatch. Wipes the targeted user's
  wizard sub-object + bumps the wizard epoch (parity
  with the user-facing reset, established by BRAIN-95).
  Admin-targeted destruction.

Failure mode (Per Huntova engineering review on CSRF +
OWASP CSRF Prevention Cheat Sheet): the same defense-in-
depth argument that motivates BRAIN-114 applies to these
two endpoints — subdomain-takeover, header-injection,
proxy bugs, future regressions widening the exempt list.
The double-submit token + SameSite=Lax already covers
the mainline case, but destructive endpoints get the
extra Origin gate.

Invariant: every destructive wizard write path is in
`_WIZARD_DESTRUCTIVE_ORIGIN_GATED_PATHS` AND CSRFMiddleware
enforces the gate on it.
"""
from __future__ import annotations
import inspect
import re


def test_complete_path_in_destructive_set():
    """`/api/wizard/complete` is destructive on retrain
    (overwrites prior brain) and must be in the set."""
    import server as _s
    paths = getattr(_s, "_WIZARD_DESTRUCTIVE_ORIGIN_GATED_PATHS", None)
    assert paths is not None
    assert "/api/wizard/complete" in paths, (
        "BRAIN-133 regression: /api/wizard/complete is "
        "destructive on retrain (overwrites the prior "
        "brain + dossier in user_settings). It must be "
        "in `_WIZARD_DESTRUCTIVE_ORIGIN_GATED_PATHS` for "
        "parity with /api/wizard/reset and "
        "/api/wizard/start-retrain."
    )


def test_admin_reset_pattern_in_destructive_set():
    """`/api/ops/users/{user_id}/wizard/reset` is the
    admin operator escape hatch. Targeted destruction.
    Because the path contains a runtime-substituted
    `{user_id}`, the middleware must enforce the gate
    via a pattern/prefix match — not just exact-set
    membership. The set may carry the literal template
    string; the middleware decides how to match."""
    import server as _s
    paths = getattr(_s, "_WIZARD_DESTRUCTIVE_ORIGIN_GATED_PATHS", None)
    assert paths is not None
    # Either the literal template, OR a sentinel showing
    # the middleware handles this path via a prefix /
    # regex match. We accept either pattern as long as
    # the runtime behavioral test below passes.
    template_present = (
        "/api/ops/users/{user_id}/wizard/reset" in paths
        or any(
            isinstance(p, str)
            and p.startswith("/api/ops/users/")
            and p.endswith("/wizard/reset")
            for p in paths
        )
    )
    # Also allow a separate prefix/regex constant if the
    # implementation chooses that route.
    src = inspect.getsource(_s)
    pattern_present = (
        "/api/ops/users/" in src
        and "/wizard/reset" in src
        and "_is_trusted_origin" in src
    )
    assert template_present or pattern_present, (
        "BRAIN-133 regression: the admin "
        "/api/ops/users/{user_id}/wizard/reset endpoint "
        "must be Origin-gated alongside the user-facing "
        "destructive wizard endpoints (BRAIN-114). The "
        "middleware must either include a template/pattern "
        "in `_WIZARD_DESTRUCTIVE_ORIGIN_GATED_PATHS` or "
        "consult a prefix/regex match for "
        "/api/ops/users/<id>/wizard/reset."
    )


def test_csrf_middleware_enforces_complete_path():
    """Source-level: the middleware must evaluate the
    Origin gate on the destructive-set membership for
    `/api/wizard/complete`. We assert this indirectly
    by confirming the middleware references the set
    AND the destructive set contains the path."""
    from server import CSRFMiddleware
    import server as _s
    src = inspect.getsource(CSRFMiddleware)
    assert "_WIZARD_DESTRUCTIVE_ORIGIN_GATED_PATHS" in src
    assert "_is_trusted_origin(" in src
    assert "/api/wizard/complete" in _s._WIZARD_DESTRUCTIVE_ORIGIN_GATED_PATHS


def test_csrf_middleware_enforces_admin_reset_path():
    """Source-level: middleware references either the
    template, a prefix/suffix check, or a regex that
    catches `/api/ops/users/<id>/wizard/reset`."""
    from server import CSRFMiddleware
    src = inspect.getsource(CSRFMiddleware)
    # The middleware must reference the admin path in
    # some matcher form so the gate fires at runtime.
    handles_admin = (
        # exact-set with template literal AND middleware
        # reformats request path to template form, OR
        # explicit substring/regex check in middleware.
        ("/api/ops/users/" in src and "/wizard/reset" in src)
        or "wizard/reset" in src  # implementation detail
    )
    # Fallback: even if the matcher lives outside the
    # middleware (in a helper), middleware must still
    # call the helper / consult the set, so we still
    # require either the path or a helper reference.
    assert handles_admin or "_admin_wizard_reset_path_re" in src or any(
        name.startswith("_is_") and "destructive" in name.lower()
        for name in dir(__import__("server"))
    ), (
        "BRAIN-133 regression: CSRFMiddleware must "
        "enforce Origin on /api/ops/users/<id>/wizard/reset."
    )


def test_bad_origin_blocked_on_complete():
    """Behavioral: a POST to /api/wizard/complete with an
    attacker Origin returns 403 bad_origin even before
    auth/CSRF token validation reaches the handler. We
    reach the gate by passing a valid CSRF token + cookie
    — which an attacker would have if SameSite/subdomain-
    takeover regressed; the Origin gate is the last
    line of defense."""
    from starlette.testclient import TestClient
    from server import app, CSRF_COOKIE_NAME
    client = TestClient(app)
    token = "xyz-test-token-1234567890abcdef"
    r = client.post(
        "/api/wizard/complete",
        headers={
            "Origin": "https://evil.example.com",
            "X-CSRF-Token": token,
        },
        cookies={CSRF_COOKIE_NAME: token},
        json={},
    )
    assert r.status_code == 403, (
        f"BRAIN-133 regression: expected 403 bad_origin "
        f"on /api/wizard/complete with attacker Origin, "
        f"got {r.status_code}. Body: {r.text[:200]}"
    )
    body = r.json()
    assert body.get("error") == "bad_origin", (
        f"Expected error=bad_origin, got {body!r}"
    )


def test_bad_origin_blocked_on_admin_reset():
    """Behavioral: a POST to /api/ops/users/42/wizard/reset
    with an attacker Origin returns 403 bad_origin."""
    from starlette.testclient import TestClient
    from server import app, CSRF_COOKIE_NAME
    client = TestClient(app)
    token = "xyz-test-token-1234567890abcdef"
    r = client.post(
        "/api/ops/users/42/wizard/reset",
        headers={
            "Origin": "https://evil.example.com",
            "X-CSRF-Token": token,
        },
        cookies={CSRF_COOKIE_NAME: token},
        json={"reason": "attack"},
    )
    assert r.status_code == 403, (
        f"BRAIN-133 regression: expected 403 bad_origin "
        f"on /api/ops/users/42/wizard/reset with attacker "
        f"Origin, got {r.status_code}. Body: {r.text[:200]}"
    )
    body = r.json()
    assert body.get("error") == "bad_origin", (
        f"Expected error=bad_origin, got {body!r}"
    )
