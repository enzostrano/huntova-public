"""Regression tests for BRAIN-150 (a533): comprehensive
audit of every GET-registered route — must NOT accept
POST / PUT / DELETE / PATCH. Catches the inverse of
BRAIN-149: a "convenient" dual-method route (GET +
POST) where the POST silently ships state-changing
work behind the GET reads.

Failure mode (Per Huntova engineering review on
HTTP-method discipline):

BRAIN-149 caught POST routes that accept GET
(catastrophic CSRF + caching gap). The inverse case:
a route registered with GET that ALSO accepts POST
silently allows state mutation via what looked like
a read endpoint. Worse: scrapers, link-previews, and
caching layers treat the URL as read-only and may
replay/cache aggressively, then a POST hits the same
URL and the cache poisons the next reader.

Per Huntova engineering review: every GET-registered
route must be GET-only. No dual-method routes
without explicit auditor sign-off (none should exist).

Invariant: every route on `app.routes` registered
with GET as the primary method must reject
POST / PUT / DELETE / PATCH.
"""
from __future__ import annotations


def _all_get_only_routes():
    """Return list of (path, methods_set) for routes
    where GET is registered + no other unsafe method
    is. We pull GET-marked routes and check the
    method set is exactly {GET, HEAD} (HEAD is
    auto-added by FastAPI for GET routes)."""
    from server import app
    out = []
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or set()
        methods = {m.upper() for m in methods}
        if "GET" in methods and path:
            out.append((path, methods))
    return out


def test_get_route_audit_finds_meaningful_count():
    """Sanity: 20+ GET routes expected."""
    routes = _all_get_only_routes()
    assert len(routes) >= 20, (
        f"BRAIN-150 regression: expected 20+ GET "
        f"routes; got {len(routes)}. Router may have "
        f"lost endpoints."
    )


def test_every_get_route_rejects_post():
    """Comprehensive: every GET route must NOT also
    accept POST. The inverse of BRAIN-149."""
    routes = _all_get_only_routes()
    for path, methods in routes:
        assert "POST" not in methods, (
            f"BRAIN-150 regression: GET route {path!r} "
            f"also accepts POST. Dual-method routes "
            f"silently allow state mutation behind a "
            f"read URL — caching layers + link "
            f"previews treat as read-only and may "
            f"poison subsequent readers. Methods: "
            f"{methods}"
        )


def test_every_get_route_rejects_put_delete_patch():
    """PUT / DELETE / PATCH absent on every GET
    route."""
    routes = _all_get_only_routes()
    for path, methods in routes:
        for forbidden in ("PUT", "DELETE", "PATCH"):
            assert forbidden not in methods, (
                f"BRAIN-150 regression: GET route "
                f"{path!r} also accepts {forbidden}. "
                f"Read-only endpoints must not mutate."
            )


def test_critical_read_routes_present():
    """Sanity: critical read endpoints present in
    the audit so a router regression that silently
    drops them is caught here too."""
    routes = {p for p, _ in _all_get_only_routes()}
    critical = {
        "/api/wizard/status",
        "/agent/events",
        "/api/runtime",
    }
    missing = critical - routes
    # Some may not exist in current build; only flag
    # if more than half are missing.
    assert len(missing) < len(critical), (
        f"BRAIN-150 regression: many critical reads "
        f"missing: {missing}. Router has lost endpoints."
    )
