"""
Huntova SaaS — Authentication
Signup, login, logout. bcrypt hashing. HttpOnly cookie sessions.
"""
import os
import secrets
import bcrypt
from fastapi import Request, HTTPException, Response
from config import SESSION_COOKIE_NAME
from itsdangerous import URLSafeTimedSerializer
import db


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    if not password or not hashed:
        return False
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


def generate_token() -> str:
    return secrets.token_urlsafe(48)



def generate_verification_token(email: str, user_id: int = 0) -> str:
    """Sign an email-verify token bound to BOTH the email and the user_id.

    Stability fix (Perplexity bug #72): the previous version signed
    only the email. After a user delete + resignup with the same
    address, an old verification link issued to the deleted user
    could verify the NEW user (get_user_by_email at redeem time
    resolves to whoever currently owns the email). Binding to
    user_id closes that. Backwards-compat: if user_id is 0 we still
    sign the legacy single-string payload so the existing email
    flow can call us without breaking.
    """
    from config import SECRET_KEY
    s = URLSafeTimedSerializer(SECRET_KEY)
    if user_id:
        return s.dumps({"email": email, "uid": int(user_id)}, salt="email-verify")
    return s.dumps(email, salt="email-verify")


def verify_verification_token(token: str, max_age: int = 86400) -> tuple[str, int] | None:
    """Return (email, user_id) tuple, or None on bad/expired token.

    user_id is 0 for legacy tokens minted before the bug-#72 fix —
    callers fall back to email-only lookup with extra care.
    """
    from config import SECRET_KEY
    s = URLSafeTimedSerializer(SECRET_KEY)
    try:
        data = s.loads(token, salt="email-verify", max_age=max_age)
    except Exception:
        return None
    if isinstance(data, dict) and data.get("email"):
        return (data.get("email"), int(data.get("uid") or 0))
    if isinstance(data, str) and data:
        return (data, 0)
    return None

def _password_hash_fingerprint(password_hash: str) -> str:
    """Short hex fingerprint of the user's password_hash. Used to bind
    reset tokens to the password state at issue time so a successful
    reset invalidates all prior tokens (Perplexity bug #70)."""
    import hashlib as _hl
    return _hl.sha256((password_hash or "").encode("utf-8")).hexdigest()[:16]


def generate_reset_token(email: str, password_hash: str = "") -> str:
    """Sign a reset token bound to the user's CURRENT password_hash.

    Stability fix (Perplexity bug #70): the previous version signed
    only the email, so tokens issued in the same 1-hour window all
    stayed valid independently. Sequence: user requests reset twice,
    uses token A successfully — token B is still valid for the rest
    of the hour, an attacker who got it can reset the password again.

    Now we embed a fingerprint of password_hash at issue time. After
    a successful reset the password_hash changes, the fingerprint
    changes, and verify_reset_token rejects every prior token.
    """
    from config import SECRET_KEY
    s = URLSafeTimedSerializer(SECRET_KEY)
    return s.dumps({"email": email, "pwf": _password_hash_fingerprint(password_hash)},
                   salt="password-reset")


def verify_reset_token(token: str, max_age: int = 3600) -> tuple[str, str] | None:
    """Return (email, password_fingerprint) on success, else None.

    Backwards-compat: legacy tokens (email-only string payload) still
    verify and return ("email", "") so old links don't all break the
    moment this fix ships. The reset endpoint additionally compares
    the fingerprint against the user's current password_hash — for
    legacy tokens, the empty fingerprint is treated as "skip the
    binding check" so the existing 1h+single-use guards still apply.
    """
    from config import SECRET_KEY
    s = URLSafeTimedSerializer(SECRET_KEY)
    try:
        data = s.loads(token, salt="password-reset", max_age=max_age)
    except Exception:
        return None
    if isinstance(data, dict) and data.get("email"):
        return (data.get("email"), data.get("pwf") or "")
    if isinstance(data, str) and data:
        # Legacy single-string token (email only).
        return (data, "")
    return None


async def signup(email: str, password: str, display_name: str = "") -> dict:
    """Create a new user. Returns user dict or raises."""
    email = email.lower().strip()
    if not email or "@" not in email or len(email) < 5:
        raise ValueError("Invalid email address")
    if len(password) < 6:
        raise ValueError("Password must be at least 6 characters")

    existing = await db.get_user_by_email(email)
    if existing:
        raise ValueError("Email already registered")

    pw_hash = hash_password(password)
    user_id = await db.create_user(email, pw_hash, display_name or email.split("@")[0])
    return await db.get_user_by_id(user_id)


# Pre-computed dummy hash for constant-time comparison (prevents timing attacks)
_DUMMY_HASH = bcrypt.hashpw(b"dummy-constant-time-padding", bcrypt.gensalt()).decode("utf-8")

async def login(email: str, password: str) -> tuple[dict, str]:
    """Authenticate user. Returns (user, session_token) or raises."""
    user = await db.get_user_by_email(email)
    if not user:
        # Constant-time: verify against pre-computed hash to prevent timing attack
        verify_password(password, _DUMMY_HASH)
        raise ValueError("Invalid email or password")
    if not user.get("password_hash"):
        # Stability fix (Perplexity bug #50): OAuth-only users
        # (Google sign-in, no local password) used to return INSTANTLY
        # here while unknown-email users paid the dummy-hash cost. That
        # made OAuth users timing-distinguishable from non-existent
        # ones — an attacker probing emails could enumerate which
        # ones use Google sign-in. Pay the same dummy bcrypt to align
        # this path with the others.
        verify_password(password, _DUMMY_HASH)
        raise ValueError("Invalid email or password")
    if not verify_password(password, user["password_hash"]):
        raise ValueError("Invalid email or password")

    if user.get("is_suspended"):
        raise ValueError("Account suspended. Contact support.")

    token = generate_token()
    await db.create_session(token, user["id"])
    await db.update_last_login(user["id"])
    return user, token


async def logout(token: str):
    """Delete session."""
    await db.delete_session(token)


def set_session_cookie(response: Response, token: str):
    """Set HttpOnly session cookie. Secure flag enabled when PUBLIC_URL uses HTTPS.

    Stability fix (multi-agent bug #8): cookie max_age now derives from
    config.SESSION_EXPIRY_HOURS instead of being hardcoded to 72. Previously
    the cookie and the DB session row could drift apart silently if config
    was changed (e.g. tightening to 24h for security) — users would see the
    cookie persist for 72h but every API call would 401 because the DB row
    expired. Both now share one source of truth.
    """
    from config import PUBLIC_URL, SESSION_EXPIRY_HOURS
    _is_prod = PUBLIC_URL.startswith("https")
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=_is_prod,
        samesite="lax",
        max_age=SESSION_EXPIRY_HOURS * 3600,
        path="/",
    )


def clear_session_cookie(response: Response):
    """Clear session cookie."""
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")


CSRF_COOKIE_NAME = "hv_csrf"


def set_csrf_cookie(response: Response):
    """Set a CSRF token cookie readable by JS (not HttpOnly).
    Double-submit cookie pattern: JS reads this and sends as X-CSRF-Token header."""
    from config import PUBLIC_URL, SESSION_EXPIRY_HOURS
    _is_prod = PUBLIC_URL.startswith("https")
    token = secrets.token_urlsafe(32)
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=token,
        httponly=False,  # JS must read this
        secure=_is_prod,
        samesite="lax",
        # Mirror the session-cookie lifetime instead of hard-coding 72h.
        # If SESSION_EXPIRY_HOURS gets tightened (e.g. to 24h for security),
        # the CSRF cookie should expire on the same schedule — otherwise
        # the CSRF cookie outlives the session and old CSRF tokens stay
        # valid for replay against future short-lived sessions.
        max_age=SESSION_EXPIRY_HOURS * 3600,
        path="/",
    )
    return token


def get_csrf_from_cookie(request: Request) -> str | None:
    """Read CSRF token from cookie."""
    return request.cookies.get(CSRF_COOKIE_NAME)


def validate_csrf(request: Request) -> bool:
    """Validate X-CSRF-Token header matches the CSRF cookie.
    Returns True if valid, False if mismatch or missing."""
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    header_token = request.headers.get("x-csrf-token")
    if not cookie_token or not header_token:
        return False
    return secrets.compare_digest(cookie_token, header_token)


def _local_display_name() -> str:
    """Best-effort friendly name for the local CLI user. Reads the
    OS username and capitalises it. Falls back to '' so the frontend
    renders a generic greeting rather than 'Hey, Local User!'."""
    import getpass
    try:
        u = getpass.getuser()
    except Exception:
        u = ""
    if not u or u in ("root", "nobody"):
        return ""
    # "enzomacbook" → "Enzomacbook"; "john_doe" → "John_Doe"; keep it simple.
    return u[:1].upper() + u[1:]


# Serialise the bootstrap so a barrage of parallel /api/* calls on
# first paint don't all race to INSERT the same email and trip the
# UNIQUE constraint. The lock is async-only — fine because
# _ensure_local_user is awaited from get_current_user. After the
# first await returns, the row exists; subsequent waiters take the
# fast `if user: return user` branch.
import asyncio as _asyncio
_local_user_lock = _asyncio.Lock()


async def _ensure_local_user() -> dict:
    """Single-user-mode auto-bootstrap.

    Local CLI: there's no signup flow, so we conjure (or fetch) a
    deterministic local user on first request. Stored in SQLite like
    any other user; ID is whatever was assigned at creation time.
    """
    name = _local_display_name() or "You"
    async with _local_user_lock:
        # Pick up existing local user if present. If its display_name
        # is the legacy hardcoded "Local User", upgrade it to the OS
        # username.
        user = await db.get_user_by_email("local@huntova.app")
        if user:
            if (user.get("display_name") or "") == "Local User" and name != "You":
                try:
                    await db.update_user(user["id"], display_name=name)
                    user["display_name"] = name
                except Exception:
                    pass
            return user
        # Bootstrap one. Password is irrelevant in single-user mode (no
        # login form), but bcrypt expects something hashable.
        pw = hash_password("__local-only__")
        try:
            uid = await db.create_user("local@huntova.app", pw, name)
        except Exception:
            # If create_user raised (e.g. a sibling waiter beat us to
            # the INSERT before we acquired the lock — shouldn't happen
            # under the lock but DB-level UNIQUE handles it anyway),
            # re-fetch the row that's now present.
            user = await db.get_user_by_email("local@huntova.app")
            if user:
                return user
            raise
        user = await db.get_user_by_id(uid)
        return user or {"id": uid, "email": "local@huntova.app", "tier": "local",
                        "credits_remaining": 0, "display_name": name}


async def get_current_user(request: Request) -> dict | None:
    """FastAPI dependency: get current user from session cookie.
    Returns user dict or None if not authenticated."""
    # Single-user CLI mode: no cookies, no signup — every request
    # belongs to the one local user.
    from runtime import CAPABILITIES
    if CAPABILITIES.single_user_mode or not CAPABILITIES.auth_enabled:
        try:
            return await _ensure_local_user()
        except Exception as e:
            if os.environ.get("HV_VERBOSE_LOGS"):
                print(f"[auth] local bootstrap failed: {e}")
            return None
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    session = await db.get_session(token)
    if not session:
        return None
    user = await db.get_user_by_id(session["user_id"])
    if not user:
        # Stability fix (Perplexity bug #62): if the user row is gone
        # (account deletion cascade race, manual DB cleanup, etc.) we
        # used to just return None and leave the session row in place.
        # Every subsequent request would re-fetch the same dead session
        # until natural expiry. Delete it now so the cookie becomes a
        # no-op for all future requests.
        await db.delete_session(token)
        return None
    if user.get("is_suspended"):
        # Suspended user — reject even if session is valid
        await db.delete_session(token)
        return None
    return user


async def require_user(request: Request) -> dict:
    """FastAPI dependency: require authenticated user. Raises 401 if not."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


async def require_admin(request: Request) -> dict:
    """FastAPI dependency: require admin or superadmin user.
    Falls back to ADMIN_EMAILS bootstrap: if the user's email is in the allowlist
    but their role is still 'user', auto-promote them to 'superadmin'."""
    user = await require_user(request)
    if user.get("role") in ("admin", "superadmin"):
        return user
    # Fallback bootstrap: auto-promote ADMIN_EMAILS users who haven't been promoted yet.
    # Require email_verified=1 so that an attacker who signs up with an
    # unverified ADMIN_EMAILS address (possible if the SMTP check is skipped
    # or the address is typo-squatted) can't claim admin by default.
    from config import ADMIN_EMAILS
    if (user.get("email", "").lower() in ADMIN_EMAILS
            and int(user.get("email_verified") or 0) == 1):
        await db.update_user(user["id"], role="superadmin")
        user["role"] = "superadmin"
        return user
    raise HTTPException(status_code=403, detail="Admin required")


async def require_superadmin(request: Request) -> dict:
    """FastAPI dependency: require superadmin user."""
    user = await require_user(request)
    if user.get("role") == "superadmin":
        return user
    # Fallback bootstrap for ADMIN_EMAILS — same verified-email gate as above.
    from config import ADMIN_EMAILS
    if (user.get("email", "").lower() in ADMIN_EMAILS
            and int(user.get("email_verified") or 0) == 1):
        await db.update_user(user["id"], role="superadmin")
        user["role"] = "superadmin"
        return user
    raise HTTPException(status_code=403, detail="Superadmin required")


# ── Entitlements ──
# Single source of truth for feature gating: feature_name → set of tiers.
# Route guards and the /api/me features dict both derive from this map so
# a tier rename or tier-to-feature change only has to happen in one place.
# CSV export intentionally open to all tiers for now (pending commercial
# decision); JSON export is Growth+.
FEATURE_TIERS: dict[str, set[str]] = {
    "agent_run":           {"free", "growth", "agency"},
    "contact_visible":     {"free", "growth", "agency"},
    "email_draft_visible": {"free", "growth", "agency"},
    "export_csv":          {"free", "growth", "agency"},
    "ai_chat":             {"growth", "agency"},
    "email_rewrite":       {"growth", "agency"},
    "research":            {"growth", "agency"},
    "smart_score":         {"growth", "agency"},
    "export_json":         {"growth", "agency"},
}


# Internal helper used by policy.py — keep here so auth stays the
# single owner of the feature→tier map. policy.py imports this
# instead of reaching into FEATURE_TIERS directly.
def _feature_allowed_for_tier(tier: str, feature: str) -> bool:
    return (tier or "free") in FEATURE_TIERS.get(feature, set())


def user_has_feature(user: dict, feature: str) -> bool:
    """Does this user's tier grant access to `feature`?

    Routes through policy.policy.feature_allowed so that local CLI mode
    short-circuits to True (BYOK users are already paying their own
    provider; Huntova doesn't gate AI features on a tier).

    Unknown features return False in cloud mode (fail closed).
    """
    from policy import policy
    return policy.feature_allowed(user, feature)


def require_feature(user: dict, feature: str) -> None:
    """Raise 403 if the user lacks `feature`.

    No-op in local CLI / BYOK mode (policy returns True).
    """
    if user_has_feature(user, feature):
        return
    allowed = FEATURE_TIERS.get(feature, set())
    label = ", ".join(t.title() for t in sorted(allowed)) or "a paid"
    raise HTTPException(status_code=403, detail=f"This feature requires {label} plan. Upgrade to access it.")


def user_features(user: dict) -> dict[str, bool]:
    """Compute a full feature-flag dict for the given user.

    Used by /api/me to ship a single authoritative entitlement map to
    the frontend, replacing hand-maintained per-feature booleans.
    """
    return {name: user_has_feature(user, name) for name in FEATURE_TIERS}
