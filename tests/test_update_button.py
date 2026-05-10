"""Regression test for BRAIN-PROD-1 (a511): the in-browser one-click
update button returned 403 {"error": "CSRF validation failed"}
whenever a user landed on the dashboard via /jarvis (or any other
HTML entry point not in the original CSRF-cookie allowlist).

Symptom: clicking "Install now" on the update banner showed an error
toast like "Could not start upgrade" because /api/update/run came
back 403. Pre-fix the cookie was only set on a tight whitelist
(/, /landing, /dashboard, /hunts, /agent, /ops, /account) so any
direct visit to /jarvis (the canonical dashboard URL) skipped the
Set-Cookie header entirely. The double-submit CSRF check then
rejected every subsequent POST.

This test asserts:
  1. /jarvis is in the cookie-set allowlist so it always carries
     a CSRF cookie out of the box.
  2. /api/update/run gates on local-mode (a412 / BRAIN-51) and
     returns the right ok=True/job_id shape on the success path.

Per Huntova engineering review on update-flow CSRF parity.
"""
from __future__ import annotations
import inspect


def test_jarvis_route_sets_csrf_cookie():
    """The CSRFMiddleware GET branch widened the allowlist to
    include /jarvis. Without this, the Update button banner posts
    /api/update/run without an hv_csrf cookie and the middleware
    returns CSRF validation failed."""
    from server import _CSRF_COOKIE_HTML_GET_ALLOWLIST
    assert "/jarvis" in _CSRF_COOKIE_HTML_GET_ALLOWLIST, (
        "BRAIN-PROD-1 regression: /jarvis must be in the CSRF-cookie "
        "GET allowlist or every Update-button click on a refreshed/"
        "deep-linked /jarvis page returns 'CSRF validation failed'."
    )
    # Belt-and-braces: the original tight whitelist routes are
    # still there.
    for path in ("/", "/landing", "/dashboard", "/hunts", "/agent",
                 "/ops", "/account"):
        assert path in _CSRF_COOKIE_HTML_GET_ALLOWLIST


def test_update_run_success_response_shape():
    """Happy path: /api/update/run returns
    {ok: True, job_id: <hex>, reused: bool}. The frontend reads
    d.job_id and starts polling /api/update/job/<id>; if the
    response shape regresses to anything else the polling loop
    breaks and the user sees an indefinite 'Running upgrade…'
    spinner."""
    import asyncio
    import os
    # Force local mode so the BRAIN-51 cloud-mode gate doesn't
    # short-circuit before we exercise the success path.
    os.environ.setdefault("APP_MODE", "local")
    # Re-import runtime so CAPABILITIES picks up APP_MODE=local
    # in case some other test ran first with cloud mode set.
    import runtime
    if runtime.CAPABILITIES.mode != "local":
        # Test relies on local-mode singleton — skip if env was
        # frozen to cloud.
        import pytest
        pytest.skip("requires APP_MODE=local")

    from server import api_update_run
    fake_user = {"id": "test", "email": "t@t.local", "tier": "free"}
    result = asyncio.run(api_update_run(fake_user))
    # Either a dict (success) or a JSONResponse (error). For the
    # success path we expect a plain dict with ok/job_id.
    assert isinstance(result, dict), (
        f"BRAIN-PROD-1 regression: /api/update/run should return a "
        f"plain dict on the local-mode success path, got "
        f"{type(result).__name__}. Frontend expects "
        f"{{ok: True, job_id: <hex>, reused: bool}}."
    )
    assert result.get("ok") is True
    assert isinstance(result.get("job_id"), str) and result["job_id"]
    assert "reused" in result


def test_csrf_middleware_widened_allowlist_documented():
    """The CSRFMiddleware source must reference _CSRF_COOKIE_HTML_GET_
    ALLOWLIST instead of the old hardcoded tuple, so the fix can't
    silently regress under a copy-paste edit."""
    from server import CSRFMiddleware
    src = inspect.getsource(CSRFMiddleware)
    assert "_CSRF_COOKIE_HTML_GET_ALLOWLIST" in src, (
        "CSRFMiddleware.dispatch must read the canonical "
        "_CSRF_COOKIE_HTML_GET_ALLOWLIST tuple — inlining the path "
        "list undoes the BRAIN-PROD-1 fix."
    )
