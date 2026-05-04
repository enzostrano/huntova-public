"""Regression tests for BRAIN-148 (a531): HTTP-method
discipline lockdown on adjacent mutating endpoints
that landed via BRAIN-139/142/144 (lead-feedback,
chat, team toggle + seed-defaults). BRAIN-131 covered
/api/wizard/* + /agent/*. BRAIN-138 covered admin/
ops/*. The recently-added endpoints need parallel
codification.

Failure mode (Per Huntova engineering review on
HTTP-method discipline):

Same as BRAIN-131 / BRAIN-138 — every mutator's
controls live in route decorators. One accidental
`@app.post` → `@app.get` swap silently reopens
CSRF/caching/prefetch attack surface.

Recently-added mutators not yet locked in by tests:
- `/api/lead-feedback` (BRAIN-139)
- `/api/chat` (BRAIN-142)
- `/api/team/seed-defaults` (BRAIN-144)
- `/api/team/{slot}/toggle` (BRAIN-144)
- `/api/wizard/start-retrain` (already in /api/wizard/*
  test suite via BRAIN-131 — confirmed locked).

Plus several /api/team/* mutators not yet audited:
- `/api/team/{slot}` (PATCH-style update via POST)

Audit them all here.

Invariants:
- Each adjacent mutating route accepts POST and rejects
  GET / PUT / DELETE / PATCH.
- Behavioral: GET against a POST-only adjacent route
  returns 405 (or auth-rejection — also acceptable
  since GET never reaches the handler body).
"""
from __future__ import annotations


_ADJACENT_MUTATING_ROUTES = {
    "/api/lead-feedback",
    "/api/chat",
    "/api/team/seed-defaults",
    "/api/team/{slot}/toggle",
}


def _route_methods_for(path: str) -> set:
    from server import app
    out: set = set()
    for route in app.routes:
        if getattr(route, "path", None) == path:
            for m in (getattr(route, "methods", None) or []):
                out.add(m.upper())
    return out


def test_adjacent_mutating_routes_accept_post():
    """Each adjacent mutating route is registered for
    POST."""
    for path in _ADJACENT_MUTATING_ROUTES:
        methods = _route_methods_for(path)
        assert methods, (
            f"BRAIN-148 regression: route {path!r} not "
            f"registered. Has it moved?"
        )
        assert "POST" in methods, (
            f"BRAIN-148 regression: {path} must accept "
            f"POST. Currently registered: {methods}"
        )


def test_adjacent_mutating_routes_reject_get():
    """A future PR swapping `@app.post` → `@app.get`
    on any adjacent mutator gets caught here."""
    for path in _ADJACENT_MUTATING_ROUTES:
        methods = _route_methods_for(path)
        assert "GET" not in methods, (
            f"BRAIN-148 regression: {path} accepts GET. "
            f"Adjacent mutating routes must be POST-only."
        )


def test_adjacent_mutating_routes_reject_other_unsafe():
    """PUT / DELETE / PATCH absent."""
    for path in _ADJACENT_MUTATING_ROUTES:
        methods = _route_methods_for(path)
        for forbidden in ("PUT", "DELETE", "PATCH"):
            assert forbidden not in methods, (
                f"BRAIN-148 regression: {path} accepts "
                f"{forbidden}. Adjacent mutators must "
                f"be POST-only."
            )


def test_get_to_adjacent_mutator_returns_405_or_auth():
    """Behavioral: GET against POST-only returns 405
    (or 401/403/404 if auth/path-param rejection runs
    first — GET never reaches the handler body either
    way)."""
    from fastapi.testclient import TestClient
    from server import app
    client = TestClient(app)
    test_paths = [
        "/api/lead-feedback",
        "/api/chat",
        "/api/team/seed-defaults",
        "/api/team/researcher/toggle",  # concrete slot
    ]
    for path in test_paths:
        resp = client.get(path)
        assert resp.status_code in (405, 401, 403, 404, 422), (
            f"BRAIN-148 regression: GET {path} returned "
            f"{resp.status_code}, expected 405 or auth/"
            f"not-found rejection. A 200 means GET "
            f"reached the handler — major CSRF + "
            f"caching gap."
        )
