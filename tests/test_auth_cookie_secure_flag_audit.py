"""BRAIN-164: auth.py cookie Secure-flag invariant audit.

`_serving_over_https()` is the single decision point for whether
session + CSRF cookies carry the `Secure` attribute. Set wrong, the
update button (and any other CSRF-protected POST) breaks silently
in browsers that drop Secure cookies on non-HTTPS origins
(Firefox <75, Safari, Brave strict).

The a586 fix (BRAIN-PROD-5) moved the gate from
`PUBLIC_URL.startswith("https")` to `CAPABILITIES.mode == "cloud"`.
These tests pin the new contract:

1. Local mode → Secure=False on every cookie helper.
2. Cloud mode → Secure=True on every cookie helper.
3. `_serving_over_https` failure-mode: returns False conservatively
   if `runtime` can't be imported (better to omit Secure than lock
   users out of the dashboard).
4. `set_csrf_cookie` + `set_session_cookie` + `clear_session_cookie`
   all use `_serving_over_https()` (no caller bypasses it).
5. Cookie attributes that must NOT depend on transport (path,
   samesite, httponly) stay invariant across modes.
"""
from __future__ import annotations

import importlib


class _FakeResponse:
    """Minimal stand-in for FastAPI's Response — captures cookies set."""

    def __init__(self):
        self.cookies = []
        self.deleted = []

    def set_cookie(self, **kwargs):
        self.cookies.append(kwargs)

    def delete_cookie(self, **kwargs):
        self.deleted.append(kwargs)


def test_serving_over_https_local_mode(local_env, monkeypatch):
    """Local mode must never claim HTTPS."""
    monkeypatch.setenv("APP_MODE", "local")
    import runtime
    importlib.reload(runtime)
    import auth
    importlib.reload(auth)
    assert auth._serving_over_https() is False


def test_serving_over_https_cloud_mode(local_env, monkeypatch):
    """Cloud mode must always claim HTTPS (Railway terminates TLS)."""
    monkeypatch.setenv("APP_MODE", "cloud")
    import runtime
    importlib.reload(runtime)
    import auth
    importlib.reload(auth)
    assert auth._serving_over_https() is True


def test_serving_over_https_falls_back_safe_on_runtime_failure(local_env, monkeypatch):
    """If runtime import raises, fall back to False (omit Secure flag)
    rather than crash. Better UX: the dashboard works on a slightly
    less-secure transport than locks the user out completely."""
    import auth
    importlib.reload(auth)
    # Simulate runtime import failure by stubbing.
    import sys
    real_runtime = sys.modules.get("runtime")
    sys.modules["runtime"] = None  # type: ignore[assignment]
    try:
        assert auth._serving_over_https() is False
    finally:
        if real_runtime is not None:
            sys.modules["runtime"] = real_runtime


def test_csrf_cookie_secure_off_in_local(local_env, monkeypatch):
    monkeypatch.setenv("APP_MODE", "local")
    import runtime
    importlib.reload(runtime)
    import auth
    importlib.reload(auth)
    resp = _FakeResponse()
    auth.set_csrf_cookie(resp)
    assert len(resp.cookies) == 1
    cookie = resp.cookies[0]
    assert cookie["secure"] is False, (
        "local mode CSRF cookie must NOT be Secure — strict browsers "
        "drop Secure cookies on http://127.0.0.1"
    )
    # JS reads CSRF, so httponly must be False even in cloud.
    assert cookie["httponly"] is False
    assert cookie["samesite"] == "lax"
    assert cookie["path"] == "/"


def test_csrf_cookie_secure_on_in_cloud(local_env, monkeypatch):
    monkeypatch.setenv("APP_MODE", "cloud")
    import runtime
    importlib.reload(runtime)
    import auth
    importlib.reload(auth)
    resp = _FakeResponse()
    auth.set_csrf_cookie(resp)
    cookie = resp.cookies[0]
    assert cookie["secure"] is True
    assert cookie["httponly"] is False
    assert cookie["samesite"] == "lax"


def test_session_cookie_secure_off_in_local(local_env, monkeypatch):
    monkeypatch.setenv("APP_MODE", "local")
    import runtime
    importlib.reload(runtime)
    import auth
    importlib.reload(auth)
    resp = _FakeResponse()
    auth.set_session_cookie(resp, "test-token-xyz")
    cookie = resp.cookies[0]
    assert cookie["secure"] is False
    # Session cookie is HttpOnly; JS must NOT read it.
    assert cookie["httponly"] is True
    assert cookie["value"] == "test-token-xyz"


def test_session_cookie_secure_on_in_cloud(local_env, monkeypatch):
    monkeypatch.setenv("APP_MODE", "cloud")
    import runtime
    importlib.reload(runtime)
    import auth
    importlib.reload(auth)
    resp = _FakeResponse()
    auth.set_session_cookie(resp, "test-token-xyz")
    cookie = resp.cookies[0]
    assert cookie["secure"] is True
    assert cookie["httponly"] is True


def test_clear_session_cookie_mirrors_secure_flag_local(local_env, monkeypatch):
    """Clear must mirror the same `Secure` attribute as set, otherwise
    the browser keeps the original cookie (set+clear with mismatched
    attributes don't replace each other)."""
    monkeypatch.setenv("APP_MODE", "local")
    import runtime
    importlib.reload(runtime)
    import auth
    importlib.reload(auth)
    resp = _FakeResponse()
    auth.clear_session_cookie(resp)
    # clear_session_cookie clears BOTH the session and CSRF cookies —
    # all must mirror Secure=False so the browser actually replaces
    # the originals (deletion only matches with identical attributes).
    assert len(resp.deleted) >= 1
    for cleared in resp.deleted:
        assert cleared.get("secure") is False, (
            f"cleared cookie {cleared.get('key')!r} must mirror Secure=False in local"
        )


def test_clear_session_cookie_mirrors_secure_flag_cloud(local_env, monkeypatch):
    monkeypatch.setenv("APP_MODE", "cloud")
    import runtime
    importlib.reload(runtime)
    import auth
    importlib.reload(auth)
    resp = _FakeResponse()
    auth.clear_session_cookie(resp)
    assert len(resp.deleted) >= 1
    for cleared in resp.deleted:
        assert cleared.get("secure") is True, (
            f"cleared cookie {cleared.get('key')!r} must mirror Secure=True in cloud"
        )


def test_csrf_cookie_uses_session_expiry_hours(local_env, monkeypatch):
    """Stability-fix invariant: CSRF cookie max_age must equal
    SESSION_EXPIRY_HOURS * 3600 — not a hardcoded number — so a
    tightened session expiry doesn't leave CSRF tokens replayable
    after the session is gone."""
    monkeypatch.setenv("APP_MODE", "cloud")
    import runtime
    importlib.reload(runtime)
    import auth
    importlib.reload(auth)
    from config import SESSION_EXPIRY_HOURS
    resp = _FakeResponse()
    auth.set_csrf_cookie(resp)
    cookie = resp.cookies[0]
    assert cookie["max_age"] == SESSION_EXPIRY_HOURS * 3600


def test_session_cookie_uses_session_expiry_hours(local_env, monkeypatch):
    """Multi-agent bug #8 fix: session cookie max_age tracks
    SESSION_EXPIRY_HOURS rather than the hardcoded 72."""
    monkeypatch.setenv("APP_MODE", "cloud")
    import runtime
    importlib.reload(runtime)
    import auth
    importlib.reload(auth)
    from config import SESSION_EXPIRY_HOURS
    resp = _FakeResponse()
    auth.set_session_cookie(resp, "tok")
    cookie = resp.cookies[0]
    assert cookie["max_age"] == SESSION_EXPIRY_HOURS * 3600


def test_csrf_cookie_returns_token(local_env, monkeypatch):
    """set_csrf_cookie returns the freshly minted token so the caller
    can include it in the response body / first-render template."""
    monkeypatch.setenv("APP_MODE", "local")
    import runtime
    importlib.reload(runtime)
    import auth
    importlib.reload(auth)
    resp = _FakeResponse()
    tok = auth.set_csrf_cookie(resp)
    assert isinstance(tok, str)
    # Must be the same token written to the cookie.
    assert resp.cookies[0]["value"] == tok
    # Reasonable length (token_urlsafe(32) → 43 chars).
    assert len(tok) >= 32


def test_secure_flag_cycles_correctly_across_modes(local_env, monkeypatch):
    """Switching APP_MODE between local and cloud must flip Secure
    on the very next cookie helper call (after a runtime + auth reload).
    Confirms no stale module-level cache of the previous answer."""
    monkeypatch.setenv("APP_MODE", "local")
    import runtime
    importlib.reload(runtime)
    import auth
    importlib.reload(auth)
    resp1 = _FakeResponse()
    auth.set_csrf_cookie(resp1)
    assert resp1.cookies[0]["secure"] is False

    monkeypatch.setenv("APP_MODE", "cloud")
    importlib.reload(runtime)
    importlib.reload(auth)
    resp2 = _FakeResponse()
    auth.set_csrf_cookie(resp2)
    assert resp2.cookies[0]["secure"] is True
