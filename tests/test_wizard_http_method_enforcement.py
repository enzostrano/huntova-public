"""Regression tests for BRAIN-131 (a500): every
wizard / agent mutating endpoint must reject GET and
accept only POST. GET must stay side-effect-free per
HTTP semantics + OWASP CSRF guidance — once a
state-changing route accepts GET, it inherits the
full CSRF, caching, prefetch, and crawler attack
surface that unsafe-method protections assume away.

Failure mode (Per Huntova engineering review on
HTTP-method discipline + OWASP CSRF Cheat Sheet):

The current router state is correct: all wizard
mutating routes use `@app.post(...)`, and FastAPI
returns 405 for GET against a POST-only route. The
CONTROLS LIVE IN ROUTE DECORATORS — one accidental
`@app.get` swap on a future refactor reopens:

- CSRF attacks via image/iframe/link prefetches that
  always issue GET (browsers don't add the CSRF
  token to those).
- Crawler / Slack / link-preview side effects that
  fire on any tab paste.
- Cached responses that turn a one-time mutation
  into a reusable URL.
- Browser back/forward + reload that accidentally
  re-trigger state changes.

Per Huntova engineering review on HTTP-method
discipline: every wizard endpoint that can mutate
state, spend quota, trigger AI work, or reset/retrain
anything must reject GET and accept only the
intended unsafe method (POST). Read endpoints can
stay GET. No mutating path gets to be
"conveniently dual-method".

Invariants:
- Mutating wizard routes (`/api/wizard/scan`,
  `/api/wizard/complete`, `/api/wizard/reset`,
  `/api/wizard/save-progress`,
  `/api/wizard/generate-phase5`,
  `/api/wizard/assist`,
  `/api/wizard/start-retrain`) AND mutating agent
  route (`/agent/control`) are registered with
  `methods={"POST"}` — GET / PUT / DELETE / PATCH
  are absent.
- `/api/wizard/status` (read) and `/agent/events`
  (SSE) are GET-only.
- A behavioral check confirms FastAPI returns 405
  Method Not Allowed when GET hits a POST-only
  route.
"""
from __future__ import annotations


_MUTATING_WIZARD_ROUTES = {
    "/api/wizard/scan",
    "/api/wizard/complete",
    "/api/wizard/reset",
    "/api/wizard/save-progress",
    "/api/wizard/generate-phase5",
    "/api/wizard/assist",
    "/api/wizard/start-retrain",
    "/agent/control",
}

_READ_ROUTES = {
    "/api/wizard/status": "GET",
    "/agent/events": "GET",
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


def test_mutating_wizard_routes_accept_post():
    """Each mutating wizard route is registered for
    POST."""
    for path in _MUTATING_WIZARD_ROUTES:
        methods = _route_methods_for(path)
        assert methods, (
            f"BRAIN-131 regression: route {path!r} not "
            f"registered. Has the path moved?"
        )
        assert "POST" in methods, (
            f"BRAIN-131 regression: {path} must accept "
            f"POST. Currently registered: {methods}"
        )


def test_mutating_wizard_routes_reject_get():
    """A future PR swapping @app.post → @app.get on
    any mutating route gets caught here."""
    for path in _MUTATING_WIZARD_ROUTES:
        methods = _route_methods_for(path)
        assert "GET" not in methods, (
            f"BRAIN-131 regression: {path} accepts GET. "
            f"Mutating routes must be POST-only — once a "
            f"state-changing route accepts GET, browsers, "
            f"crawlers, prefetchers, and cached links can "
            f"all trigger the side effect."
        )


def test_mutating_wizard_routes_reject_other_unsafe_methods():
    """Defense-in-depth: PUT / DELETE / PATCH should
    also be absent. The handlers only know how to
    interpret POST bodies."""
    for path in _MUTATING_WIZARD_ROUTES:
        methods = _route_methods_for(path)
        for forbidden in ("PUT", "DELETE", "PATCH"):
            assert forbidden not in methods, (
                f"BRAIN-131 regression: {path} accepts "
                f"{forbidden}. Mutating wizard routes "
                f"must be POST-only."
            )


def test_read_routes_are_get_only():
    """Read endpoints stay GET-only — they don't need
    to accept other methods."""
    for path, expected in _READ_ROUTES.items():
        methods = _route_methods_for(path)
        assert methods, f"Route {path!r} not registered"
        assert expected in methods
        # Read routes must NOT accept POST/PUT/DELETE/
        # PATCH — those would imply a hidden mutation
        # path.
        for unsafe in ("POST", "PUT", "DELETE", "PATCH"):
            assert unsafe not in methods, (
                f"BRAIN-131 regression: read route {path} "
                f"unexpectedly accepts {unsafe}. Read "
                f"routes must be GET-only."
            )


def test_get_request_to_mutating_route_returns_405():
    """Behavioral: actually issue a GET against a
    POST-only route via Starlette TestClient and
    assert 405. Catches a regression where the
    FastAPI router is configured weirdly enough that
    `app.routes` shows POST-only but the runtime
    accepts GET anyway."""
    from fastapi.testclient import TestClient
    from server import app
    client = TestClient(app)
    for path in _MUTATING_WIZARD_ROUTES:
        resp = client.get(path)
        # 405 Method Not Allowed is the expected
        # response. Some routes may 401/403 first if
        # auth dep runs before the method check, but
        # those are also acceptable — the GET can't
        # mutate state because it never reaches the
        # handler body.
        assert resp.status_code in (405, 401, 403, 422), (
            f"BRAIN-131 regression: GET {path} returned "
            f"{resp.status_code}, expected 405 (or auth "
            f"rejection 401/403/422 before method check). "
            f"A 200 here means a mutating route is "
            f"accepting GET — major CSRF + caching gap."
        )


def test_post_request_to_post_only_route_does_not_405():
    """Sanity check the inverse: POST to a POST-only
    route must NOT return 405. Otherwise the route
    isn't actually accepting POST."""
    from fastapi.testclient import TestClient
    from server import app
    client = TestClient(app)
    for path in _MUTATING_WIZARD_ROUTES:
        resp = client.post(path, json={})
        assert resp.status_code != 405, (
            f"BRAIN-131 regression: POST {path} "
            f"returned 405 — route accepts wrong method "
            f"set. Got: {resp.status_code}"
        )
