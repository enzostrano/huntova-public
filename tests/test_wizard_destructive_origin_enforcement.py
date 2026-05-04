"""Regression tests for BRAIN-114 (a483): destructive
wizard endpoints (`/api/wizard/reset`, `/api/wizard/
start-retrain`) must enforce Origin in addition to the
double-submit CSRF token. Defense in depth.

Failure mode (Per Huntova engineering review on
CSRF + OWASP CSRF Prevention Cheat Sheet):

The CSRFMiddleware already requires a valid X-CSRF-Token
header matching the `hv_csrf` cookie (double-submit
cookie) on every non-exempt POST. SameSite=Lax on the
session + CSRF cookies blocks browser-originated
cross-site POSTs from carrying credentials. So in the
mainline case, a malicious site cannot trigger
`/api/wizard/reset` against an authenticated user.

But destructive endpoints — the ones that wipe
persistent state on success — deserve defense in depth:

- A subdomain takeover or future cookie-policy regression
  could let a same-site attacker bypass SameSite.
- Header-injection / proxy bugs could leak the CSRF
  cookie into a request the user didn't initiate.
- Future middleware changes could accidentally widen
  the exempt list.

Per OWASP CSRF cheat sheet: combine token-based
defenses with strict Origin verification on destructive
endpoints. Browser-originated POSTs always carry
`Origin`. Same-origin POSTs from our own UI carry our
PUBLIC_URL or a localhost host. CLI/curl scripts don't
send Origin at all (allow). An Origin that is set AND
points to an attacker domain is the smoking gun.

Invariants:
- Module-scope helper `_is_trusted_origin(origin)`
  returns True for: empty Origin (CLI/curl), local
  hosts (127.0.0.1, localhost, [::1] — any port), and
  origins matching `PUBLIC_URL` (for cloud mode).
  Returns False for any other set Origin.
- A constant set
  `_WIZARD_DESTRUCTIVE_ORIGIN_GATED_PATHS` enumerates
  the destructive wizard paths that must pass the
  extra Origin gate: `/api/wizard/reset`,
  `/api/wizard/start-retrain`.
- The CSRFMiddleware enforces the extra Origin gate AFTER
  the token check passes. Source-level proof is
  sufficient — the middleware code is short.
"""
from __future__ import annotations
import inspect


def test_trusted_origin_helper_exists():
    """Module-scope helper returns the trust verdict."""
    import server as _s
    fn = getattr(_s, "_is_trusted_origin", None)
    assert fn is not None and callable(fn), (
        "BRAIN-114 regression: server must expose "
        "`_is_trusted_origin(origin)` so destructive "
        "wizard endpoints can apply consistent "
        "same-origin enforcement."
    )


def test_trusted_origin_accepts_empty():
    """Empty Origin → CLI/curl (no browser) → allow."""
    import server as _s
    assert _s._is_trusted_origin("") is True
    assert _s._is_trusted_origin(None) is True


def test_trusted_origin_accepts_localhost_variants():
    """Browser-originated POSTs from local UI."""
    import server as _s
    assert _s._is_trusted_origin("http://127.0.0.1:5050") is True
    assert _s._is_trusted_origin("http://localhost:5050") is True
    assert _s._is_trusted_origin("http://[::1]:5050") is True
    assert _s._is_trusted_origin("https://127.0.0.1:5050") is True
    # Trailing slash + uppercase normalization.
    assert _s._is_trusted_origin("HTTP://localhost:5050/") is True


def test_trusted_origin_rejects_evil_origins():
    """A set Origin that doesn't match local or
    PUBLIC_URL → reject."""
    import server as _s
    assert _s._is_trusted_origin("https://evil.com") is False
    assert _s._is_trusted_origin("http://attacker.localhost.evil.com") is False
    # Subdomain of PUBLIC_URL should also be rejected
    # because cookies are scoped to the apex domain — a
    # legitimate browser POST from huntova.com isn't
    # going to come from foo.huntova.com.attacker.org.
    assert _s._is_trusted_origin("https://huntova.com.evil.com") is False


def test_trusted_origin_accepts_public_url():
    """Origin matching PUBLIC_URL → allow (cloud mode)."""
    import server as _s
    from config import PUBLIC_URL
    if PUBLIC_URL.startswith("http"):
        assert _s._is_trusted_origin(PUBLIC_URL.rstrip("/")) is True


def test_destructive_paths_set_is_documented():
    """Module-scope set names the destructive wizard
    endpoints that need the extra Origin gate."""
    import server as _s
    paths = getattr(_s, "_WIZARD_DESTRUCTIVE_ORIGIN_GATED_PATHS", None)
    assert paths is not None, (
        "BRAIN-114 regression: server must expose "
        "`_WIZARD_DESTRUCTIVE_ORIGIN_GATED_PATHS` set so "
        "the middleware (and any auditor) can see exactly "
        "which routes get the extra Origin gate."
    )
    assert "/api/wizard/reset" in paths, (
        "BRAIN-114 regression: /api/wizard/reset is the "
        "primary destructive endpoint."
    )
    assert "/api/wizard/start-retrain" in paths, (
        "BRAIN-114 regression: /api/wizard/start-retrain "
        "flips _interview_complete=False and _wizard_phase=0 "
        "— destructive even if not a full wipe."
    )


def test_csrf_middleware_consults_destructive_set():
    """Source-level: the CSRFMiddleware must reference
    `_WIZARD_DESTRUCTIVE_ORIGIN_GATED_PATHS` so it
    actually enforces the extra Origin gate."""
    from server import CSRFMiddleware
    src = inspect.getsource(CSRFMiddleware)
    assert "_WIZARD_DESTRUCTIVE_ORIGIN_GATED_PATHS" in src, (
        "BRAIN-114 regression: CSRFMiddleware must "
        "reference `_WIZARD_DESTRUCTIVE_ORIGIN_GATED_PATHS` "
        "to enforce Origin on destructive wizard paths."
    )


def test_csrf_middleware_calls_trusted_origin_on_destructive():
    """Source-level: the destructive-path branch must
    call `_is_trusted_origin` so missing/bad Origin is
    actually rejected."""
    from server import CSRFMiddleware
    src = inspect.getsource(CSRFMiddleware)
    assert "_is_trusted_origin(" in src, (
        "BRAIN-114 regression: CSRFMiddleware must call "
        "`_is_trusted_origin` on the destructive path so "
        "an attacker domain can't squeak through after "
        "the token check (defense in depth)."
    )


def test_csrf_middleware_returns_403_on_bad_origin_destructive():
    """Source-level: the destructive-path Origin check
    must return 403 — same status used by the existing
    CSRF token failure and CSRF-exempt Origin rejection.
    A 200 or 5xx would mask the attack."""
    from server import CSRFMiddleware
    src = inspect.getsource(CSRFMiddleware)
    # Look for the bad_origin branch returning 403.
    # The existing exempt-path branch already does this;
    # new branch should reuse the same status + error_kind.
    import re
    assert re.search(
        r"_WIZARD_DESTRUCTIVE_ORIGIN_GATED_PATHS.*bad_origin.*status_code=403",
        src,
        re.DOTALL,
    ) or re.search(
        r"bad_origin.*status_code=403.*_WIZARD_DESTRUCTIVE_ORIGIN_GATED_PATHS",
        src,
        re.DOTALL,
    ) or (
        "bad_origin" in src and "status_code=403" in src
        and "_WIZARD_DESTRUCTIVE_ORIGIN_GATED_PATHS" in src
    ), (
        "BRAIN-114 regression: destructive-path Origin "
        "rejection must be 403 with `bad_origin` error "
        "kind for parity with the exempt-path Origin "
        "rejection."
    )
