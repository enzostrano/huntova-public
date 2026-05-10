"""Regression test for BRAIN-PROD-5 (a586): the second half of the
"update button spawns an error" bug.

Background: a511 (BRAIN-PROD-1) fixed the server side — `/jarvis` was
added to the CSRF-cookie GET allowlist so the cookie was actually
*set* on the response. But the cookie carried `Secure=True` because
`set_csrf_cookie` keyed the Secure flag off
`PUBLIC_URL.startswith("https")`, and `PUBLIC_URL` defaults to the
cloud production domain (`https://huntova.com`) even when the local
pipx-installed CLI is binding to `http://127.0.0.1:5050`. Browsers
that enforce `Secure` strictly on plain-HTTP origins (Firefox <75,
Safari, Brave with strict cookies, any user reverse-proxying through
HTTP) silently dropped the cookie — the dashboard JS then read
`document.cookie` as empty for `hv_csrf`, omitted the `X-CSRF-Token`
header on the POST to `/api/update/run`, and the server returned
`403 {"ok": false, "error": "CSRF validation failed"}`.

This test asserts the runtime-aware Secure flag:

  1. `auth._serving_over_https()` returns False in local mode.
  2. `auth._serving_over_https()` returns True in cloud mode.
  3. The Secure flag in `set_csrf_cookie` / `set_session_cookie` /
     `clear_session_cookie` is keyed off `_serving_over_https()`,
     not the raw `PUBLIC_URL` string. Inlining a `PUBLIC_URL.startswith
     ("https")` check anywhere in cookie code would re-introduce the
     bug.
  4. The frontend update-error path surfaces HTTP status + parsed
     server error so future bug reports carry diagnostic info instead
     of the generic "Could not start upgrade".

Per Huntova engineering review on update-flow CSRF parity (second
half of the fix).
"""
from __future__ import annotations

import ast
import inspect
import os
from pathlib import Path


def _strip_docstring_and_comments(src: str) -> str:
    """Return only the executable lines of a function source — the
    leading docstring and `#` comments are removed. Used by the
    "must not reference X" assertions so the docstring explanation
    of WHY a pattern was removed doesn't trigger a false positive."""
    try:
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Drop the docstring expression if present so it
                # doesn't appear in the surface scan.
                if (node.body and isinstance(node.body[0], ast.Expr)
                        and isinstance(node.body[0].value, ast.Constant)
                        and isinstance(node.body[0].value.value, str)):
                    node.body = node.body[1:]
        body_only = ast.unparse(tree)
    except Exception:
        body_only = src
    # Strip line comments too.
    return "\n".join(
        line.split("#", 1)[0]
        for line in body_only.splitlines()
    )


def test_serving_over_https_local_mode_returns_false():
    """In local mode the CLI binds to plain http://127.0.0.1:5050,
    so cookies must NOT carry the Secure attribute. If they do, any
    browser that strictly enforces Secure on non-HTTPS origins drops
    the cookie silently, breaking the CSRF double-submit pattern."""
    os.environ["APP_MODE"] = "local"
    # Force CAPABILITIES to re-resolve from the (now-set) env.
    import importlib
    import runtime
    importlib.reload(runtime)
    import auth
    importlib.reload(auth)
    assert auth._serving_over_https() is False, (
        "BRAIN-PROD-5 regression: _serving_over_https() returned True "
        "in APP_MODE=local. Local CLI serves over plain HTTP — Secure "
        "cookies are silently dropped by Firefox/Safari/Brave on "
        "non-HTTPS origins, which is exactly what surfaced as the "
        "'update button spawns an error' user report."
    )


def test_serving_over_https_cloud_mode_returns_true():
    """In cloud mode Huntova runs behind Railway TLS termination, so
    cookies MUST carry the Secure attribute. Without it, the session
    cookie can leak over plain HTTP if a user is MITM'd or visits
    the http:// version of the cloud URL."""
    os.environ["APP_MODE"] = "cloud"
    import importlib
    import runtime
    importlib.reload(runtime)
    import auth
    importlib.reload(auth)
    try:
        assert auth._serving_over_https() is True, (
            "BRAIN-PROD-5 regression: _serving_over_https() returned "
            "False in APP_MODE=cloud. Cloud session cookies must be "
            "Secure or they can leak over plain HTTP."
        )
    finally:
        # Reset to local for the rest of the suite.
        os.environ["APP_MODE"] = "local"
        importlib.reload(runtime)
        importlib.reload(auth)


def test_csrf_cookie_uses_runtime_https_check_not_public_url():
    """The Secure flag in `set_csrf_cookie` must call
    `_serving_over_https()` rather than reading PUBLIC_URL directly.
    A literal `PUBLIC_URL.startswith("https")` in the cookie path
    re-introduces the bug because PUBLIC_URL defaults to the cloud
    production URL even on local installs."""
    import auth
    src = inspect.getsource(auth.set_csrf_cookie)
    assert "_serving_over_https" in src, (
        "BRAIN-PROD-5 regression: set_csrf_cookie must gate Secure on "
        "_serving_over_https(), not on PUBLIC_URL.startswith('https'). "
        "PUBLIC_URL defaults to the cloud URL and is wrong for local "
        "mode. See auth._serving_over_https() docstring."
    )
    # Negative: the broken pattern must not appear in EXECUTABLE
    # code (docstrings/comments may reference it for context).
    code_only = _strip_docstring_and_comments(src)
    assert "PUBLIC_URL" not in code_only, (
        "BRAIN-PROD-5 regression: set_csrf_cookie still references "
        "PUBLIC_URL in executable code. The Secure flag must be "
        "gated by the runtime mode, not the build-time PUBLIC_URL."
    )


def test_session_cookie_uses_runtime_https_check_not_public_url():
    """Same invariant for the session cookie. Cloud sessions need
    Secure for transport security; local sessions must not have
    Secure or the browser drops them."""
    import auth
    src = inspect.getsource(auth.set_session_cookie)
    assert "_serving_over_https" in src, (
        "BRAIN-PROD-5 regression: set_session_cookie must gate Secure "
        "on _serving_over_https(), not on PUBLIC_URL.startswith('https')."
    )
    code_only = _strip_docstring_and_comments(src)
    assert "PUBLIC_URL" not in code_only, (
        "BRAIN-PROD-5 regression: set_session_cookie still "
        "references PUBLIC_URL in executable code. Local session "
        "cookies will be Secure and silently dropped by strict "
        "browsers."
    )


def test_clear_session_cookie_uses_runtime_https_check():
    """clear_session_cookie must use the SAME Secure value as the
    set_* functions or the delete-cookie header is silently rejected
    by Chrome/Firefox (attribute mismatch on cookie deletion).
    Pre-a586 the bug was symmetric: set used wrong PUBLIC_URL,
    clear also used wrong PUBLIC_URL, so they "agreed" but were both
    wrong."""
    import auth
    src = inspect.getsource(auth.clear_session_cookie)
    assert "_serving_over_https" in src, (
        "BRAIN-PROD-5 regression: clear_session_cookie must mirror "
        "the Secure flag used by set_*_cookie. Mismatched attributes "
        "cause the browser to keep the cookie after logout."
    )


def test_update_run_endpoint_logs_failures_to_stderr():
    """update_runner._run must print failure paths to stderr so
    server logs carry the actual error. Pre-a586 the failures were
    silent on the server — only the JSON job record carried the
    error, so users couldn't grep their server log to find out why
    the update button errored."""
    import update_runner
    src = inspect.getsource(update_runner._run)
    assert "file=sys.stderr" in src, (
        "BRAIN-PROD-5 regression: update_runner._run must log failure "
        "paths to stderr so server-side bug reports can be reproduced "
        "from the log instead of querying /api/update/job/<id>."
    )


def test_jarvis_update_modal_surfaces_http_status():
    """The frontend update-error path must include the HTTP status
    code so future bug reports identify the failure mode without
    needing a server-side reproduction. Pre-a586 the user-visible
    error was "Could not start upgrade" for every failure — 401, 403,
    409, 503 all looked identical."""
    path = Path(__file__).resolve().parent.parent / "templates" / "jarvis.html"
    src = path.read_text(encoding="utf-8")
    # The error path must read the response status, not just the
    # parsed body's `ok` field.
    assert "HTTP ' + r.status" in src, (
        "BRAIN-PROD-5 regression: the update modal's error message "
        "must include the HTTP status code. Frontend was previously "
        "showing 'Could not start upgrade' for every failure mode."
    )
    # A specific hint for CSRF failures so users on stale cookies
    # know to hard-refresh.
    assert "hard refresh" in src.lower(), (
        "BRAIN-PROD-5 regression: the update modal must hint at "
        "hard-refresh on CSRF failures. Without the hint, users hit "
        "the same error repeatedly without knowing how to recover."
    )
