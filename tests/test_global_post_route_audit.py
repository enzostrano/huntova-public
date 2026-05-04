"""Regression tests for BRAIN-149 (a532): comprehensive
HTTP-method audit covering EVERY `@app.post` route in
server.py. BRAIN-131 / BRAIN-138 / BRAIN-148 codified
specific groups (wizard, agent, ops, adjacent). This
release is the catch-all: every POST-registered route
must reject GET / PUT / DELETE / PATCH.

Failure mode (Per Huntova engineering review on
HTTP-method discipline blanket coverage):

The per-group lockdown tests (BRAIN-131/138/148)
codify specific path lists. New endpoints added
between those groups slip through until someone
remembers to extend an audit list. A blanket audit
that pulls EVERY `@app.post`-decorated route from
the live FastAPI app and asserts the contract on
each one closes the coverage gap.

Invariants:
- Every route registered with the POST method on
  the FastAPI app rejects GET / PUT / DELETE /
  PATCH.
- The audit count is non-trivial (60+ POST routes
  in current server.py).
"""
from __future__ import annotations


def _all_post_routes():
    """Return a list of (path, methods_set) for every
    route registered on the app where POST is one of
    the methods."""
    from server import app
    out = []
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or set()
        methods = {m.upper() for m in methods}
        if "POST" in methods and path:
            out.append((path, methods))
    return out


def test_post_route_audit_finds_meaningful_count():
    """Sanity: there should be 30+ POST routes in
    server.py. If this drops dramatically, something's
    been silently removed."""
    routes = _all_post_routes()
    assert len(routes) >= 30, (
        f"BRAIN-149 regression: expected 30+ POST "
        f"routes; got {len(routes)}. The router may "
        f"have lost endpoints."
    )


def test_every_post_route_rejects_get():
    """Comprehensive: every POST-registered route must
    NOT also accept GET. A POST route that also
    accepts GET is a state-changing endpoint reachable
    via cached link / image-prefetch / link-preview —
    catastrophic CSRF + caching gap."""
    routes = _all_post_routes()
    for path, methods in routes:
        assert "GET" not in methods, (
            f"BRAIN-149 regression: POST route {path!r} "
            f"also accepts GET. State-changing endpoints "
            f"must be POST-only. Methods registered: "
            f"{methods}"
        )


def test_every_post_route_rejects_other_unsafe_methods():
    """Comprehensive: PUT / DELETE / PATCH absent on
    every POST route."""
    routes = _all_post_routes()
    for path, methods in routes:
        for forbidden in ("PUT", "DELETE", "PATCH"):
            assert forbidden not in methods, (
                f"BRAIN-149 regression: POST route "
                f"{path!r} also accepts {forbidden}. "
                f"Mutating routes must be POST-only."
            )


def test_post_routes_include_known_critical_endpoints():
    """Sanity: the audit captured the critical
    mutating endpoints — wizard, agent, ops, settings,
    chat, lead-feedback, team. If any of these went
    missing, BRAIN-131/138/148/this audit would all
    silently pass."""
    routes = {p for p, _ in _all_post_routes()}
    critical = {
        "/api/wizard/complete",
        "/api/wizard/save-progress",
        "/api/wizard/scan",
        "/api/wizard/reset",
        "/api/wizard/start-retrain",
        "/api/wizard/generate-phase5",
        "/api/wizard/assist",
        "/agent/control",
        "/api/chat",
        "/api/lead-feedback",
        "/api/setup/key",
        "/api/settings",
        "/api/team/seed-defaults",
        "/api/memory",
    }
    missing = critical - routes
    assert not missing, (
        f"BRAIN-149 regression: critical POST routes "
        f"missing from the audit: {missing}. The "
        f"router has lost endpoints — investigate."
    )
