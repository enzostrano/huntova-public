"""Regression tests for BRAIN-138 (a512): HTTP-method
discipline on admin / ops mutating routes. Extends
BRAIN-131's lockdown from /api/wizard/* + /agent/* to
the operator surface.

Failure mode (Per Huntova engineering review on
HTTP-method discipline + OWASP CSRF Cheat Sheet):

The router state for admin/ops routes is currently
correct: all mutators use `@app.post`, all reads use
`@app.get`. But like BRAIN-131, the controls live
entirely in route decorators. One accidental
`@app.post` → `@app.get` swap on a future refactor
on a /api/ops/users/{id}/credits or /wizard/reset
would silently reopen the destructive-action-via-
GET attack surface. Operator routes amplify the
blast radius — credit injection, user suspension,
session clearing, wizard wipe — so the
regression-test lockdown is even more important
here than on the wizard surface.

Invariants:
- Mutating ops routes (rerun-pass3, credits, plan,
  verify, suspend, sessions/clear, wizard/reset,
  agent/stop) accept POST and reject GET / PUT /
  DELETE / PATCH.
- Mutating admin routes (cloud-token) same.
- Internal _metric POST endpoint (telemetry) same.
- Read ops routes (summary, users, users/{id},
  billing, agents, audit, runs, incidents,
  metrics, health, users/{id}/events) are GET-
  only.
"""
from __future__ import annotations


_MUTATING_ADMIN_OPS_ROUTES = {
    "/api/ops/rerun-pass3",
    "/api/ops/users/{user_id}/credits",
    "/api/ops/users/{user_id}/plan",
    "/api/ops/users/{user_id}/verify",
    "/api/ops/users/{user_id}/suspend",
    "/api/ops/users/{user_id}/sessions/clear",
    "/api/ops/users/{user_id}/wizard/reset",
    "/api/ops/users/{user_id}/agent/stop",
    "/api/admin/cloud-token",
    "/api/_metric",
}

_READ_ADMIN_OPS_ROUTES = {
    "/api/admin/metrics",
    "/api/ops/summary",
    "/api/ops/users",
    "/api/ops/users/{user_id}",
    "/api/ops/billing",
    "/api/ops/agents",
    "/api/ops/users/{user_id}/events",
    "/api/ops/audit",
    "/api/ops/runs",
    "/api/ops/runs/{run_id}",
    "/api/ops/incidents",
    "/api/ops/metrics",
    "/api/ops/health",
}


def _route_methods_for(path: str) -> set:
    """Return the set of HTTP methods registered for
    `path` on the FastAPI app."""
    from server import app
    out: set = set()
    for route in app.routes:
        if getattr(route, "path", None) == path:
            for m in (getattr(route, "methods", None) or []):
                out.add(m.upper())
    return out


def test_mutating_admin_ops_routes_accept_post():
    """Each mutating admin/ops route is registered for
    POST."""
    for path in _MUTATING_ADMIN_OPS_ROUTES:
        methods = _route_methods_for(path)
        assert methods, (
            f"BRAIN-138 regression: route {path!r} not "
            f"registered. Has the path moved?"
        )
        assert "POST" in methods, (
            f"BRAIN-138 regression: {path} must accept "
            f"POST. Currently registered: {methods}"
        )


def test_mutating_admin_ops_routes_reject_get():
    """A future PR swapping @app.post → @app.get on
    any operator mutator gets caught here. Operator
    routes amplify blast radius (credit injection,
    user suspension, session clearing, wizard wipe)
    so the discipline matters even more than on the
    wizard surface."""
    for path in _MUTATING_ADMIN_OPS_ROUTES:
        methods = _route_methods_for(path)
        assert "GET" not in methods, (
            f"BRAIN-138 regression: {path} accepts GET. "
            f"Operator mutating routes must be POST-only "
            f"— a destructive admin action triggered by "
            f"a cached link, image-prefetch, or browser "
            f"reload would have catastrophic blast "
            f"radius."
        )


def test_mutating_admin_ops_routes_reject_other_unsafe_methods():
    """Defense-in-depth: PUT / DELETE / PATCH should
    also be absent."""
    for path in _MUTATING_ADMIN_OPS_ROUTES:
        methods = _route_methods_for(path)
        for forbidden in ("PUT", "DELETE", "PATCH"):
            assert forbidden not in methods, (
                f"BRAIN-138 regression: {path} accepts "
                f"{forbidden}. Operator mutators must be "
                f"POST-only."
            )


def test_read_admin_ops_routes_are_get_only():
    """Read endpoints stay GET-only."""
    for path in _READ_ADMIN_OPS_ROUTES:
        methods = _route_methods_for(path)
        assert methods, f"Route {path!r} not registered"
        assert "GET" in methods
        for unsafe in ("POST", "PUT", "DELETE", "PATCH"):
            assert unsafe not in methods, (
                f"BRAIN-138 regression: read route {path} "
                f"unexpectedly accepts {unsafe}. Read "
                f"routes must be GET-only — adding POST "
                f"would imply a hidden mutation path."
            )


def test_get_to_mutating_admin_ops_returns_405():
    """Behavioral via Starlette TestClient: GET to a
    POST-only operator route returns 405 (or auth
    rejection before method check — also fine since
    GET never reaches the handler body)."""
    from fastapi.testclient import TestClient
    from server import app
    client = TestClient(app)
    # Test a representative mutating route with a
    # concrete URL (substitute {user_id} with 1).
    test_paths = [
        "/api/ops/users/1/credits",
        "/api/ops/users/1/plan",
        "/api/ops/users/1/wizard/reset",
        "/api/ops/users/1/agent/stop",
        "/api/ops/rerun-pass3",
        "/api/admin/cloud-token",
    ]
    for path in test_paths:
        resp = client.get(path)
        assert resp.status_code in (405, 401, 403, 404, 422), (
            f"BRAIN-138 regression: GET {path} returned "
            f"{resp.status_code}, expected 405 or "
            f"auth/not-found rejection. A 200 or 5xx "
            f"means the GET reached the handler — major "
            f"blast-radius gap."
        )
