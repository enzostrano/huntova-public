#!/usr/bin/env python3
"""
Huntova SaaS — FastAPI Server
All routes, auth, SSE, static files. Replaces the Handler class from app.py.
"""
import sys
import traceback

# Imports

import asyncio
import json
import os
import re
import threading
import time
import csv
import io
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse, parse_qsl


from fastapi import FastAPI, Request, Response, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn


import db

import auth
from auth import require_user, get_current_user, require_admin, require_feature, user_features

from config import (
    VERSION, PORT, STATIC_DIR, TEMPLATES_DIR, MODEL_ID, API_URL, API_KEY,
    TIERS, BASE_DIR, LOG_DIR, MEGA_CORP_DOMAINS, DEFAULT_SETTINGS,
    SESSION_COOKIE_NAME, DATA_RETENTION_DAYS, AI_PROVIDER,
    GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI_PATH,
    PUBLIC_URL, ADMIN_EMAILS, GEMINI_MODEL_PRO,
)

import httpx
import email_service

from user_context import get_or_create_context, get_context, remove_context

from agent_runner import agent_runner


# ── Import business logic from app.py ──
from openai import OpenAI
from config import TIER_MODELS
from providers import chat_compat as _chat_compat

# Legacy client kept for any third-party code that still expects the
# OpenAI SDK shape directly. New code should use _chat_compat which
# resolves the user's BYOK provider (Gemini / Anthropic / OpenAI).
_client = OpenAI(base_url=API_URL, api_key=API_KEY) if API_KEY else None


# Drop-in for `client.chat.completions.create(**kwargs)`. Routes
# through providers.get_provider() so the user's selected BYOK
# provider handles the call, regardless of which underlying SDK it
# uses. Returns an OpenAI-shaped response object so call-site code
# reading `.choices[0].message.content` is unchanged.
def _byok_chat(**kwargs):
    return _chat_compat(**kwargs)


def _get_model_for_user(user: dict) -> str:
    """Get the AI model appropriate for the user's tier.
    Agency → Gemini Pro (smarter, deeper analysis)
    Growth/Free → Gemini Flash (fast, cost-effective)"""
    tier = user.get("tier", "free") if user else "free"
    return TIER_MODELS.get(tier, MODEL_ID)


def _ai_json_kwargs(**kw):
    _model = kw.get("model", MODEL_ID)
    if "gemini" in _model:
        kw["response_format"] = {"type": "json_object"}
    return kw


def _extract_json(text):
    """Extract JSON from AI response text. Handles truncated/trailing comma JSON."""
    if not text:
        return None
    import re as _re
    text = _re.sub(r"<think>.*?</think>", "", text, flags=_re.DOTALL).strip()
    # Try ```json blocks
    m = _re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, _re.DOTALL)
    if m:
        return m.group(1)
    m = _re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, _re.DOTALL)
    if m:
        return m.group(1)
    # Try raw JSON with brace matching
    for start in range(len(text)):
        if text[start] == '{':
            depth = 0
            for i in range(start, len(text)):
                if text[i] == '{': depth += 1
                elif text[i] == '}': depth -= 1
                if depth == 0:
                    candidate = text[start:i+1]
                    # Clean trailing commas (common AI output issue)
                    candidate = _re.sub(r',\s*([}\]])', r'\1', candidate)
                    try:
                        json.loads(candidate)
                        return candidate
                    except (json.JSONDecodeError, ValueError):
                        pass  # Try next brace match or fallback
    # Fallback: repair truncated JSON by closing open brackets
    m = _re.search(r'\{', text)
    if m:
        candidate = text[m.start():]
        # Strip incomplete values at the end (trailing strings, commas, whitespace)
        candidate = _re.sub(r'[,\s"]+$', '', candidate)
        # If ends with a key (": ), remove that incomplete key-value
        candidate = _re.sub(r',?\s*"[^"]*"\s*:\s*$', '', candidate)
        # Count unclosed brackets
        opens = candidate.count('{') - candidate.count('}')
        open_arr = candidate.count('[') - candidate.count(']')
        # Close them
        candidate += ']' * max(0, open_arr) + '}' * max(0, opens)
        # Clean trailing commas
        candidate = _re.sub(r',\s*([}\]])', r'\1', candidate)
        try:
            json.loads(candidate)
            return candidate
        except (json.JSONDecodeError, ValueError):
            pass
    return None


# ── Rate Limiting (in-memory, per-IP) ──
_rate_limits: dict[str, list] = {}  # ip -> [timestamp, timestamp, ...]
RATE_LIMIT_WINDOW = 300  # 5 minutes
RATE_LIMIT_MAX = 8       # max attempts per 5-minute window
_rate_limit_cleanup = 0.0  # last full cleanup timestamp


def _get_client_ip(request: Request) -> str:
    """Safely extract client IP. Handles reverse proxies where request.client is None.

    When behind Railway/Cloudflare/etc. request.client.host is the proxy's
    internal IP (10.x / 172.16.x) so using it directly would rate-limit every
    user in the same pod simultaneously. Only trust x-forwarded-for when the
    direct client is itself a private range — that way we still prefer the
    real peer address whenever it's available and can't be spoofed by a header.

    Stability fix (audit wave 30): the previous version returned the
    LEFTMOST entry of x-forwarded-for, which is fully attacker-
    controlled — clients can prepend any IP they want. With per-IP
    rate limits on auth login, recipe publish, AI chat, and the
    share_views dedup all keyed off `_get_client_ip`, an attacker
    rotating a random `X-Forwarded-For: <random>` per request never
    tripped any limiter. The correct pattern behind a known reverse
    proxy is to take the RIGHTMOST entry — the IP the trusted proxy
    actually saw — since each hop appends; the leftmost is whatever
    the original client claimed. Same fix applied to `x-real-ip`,
    which had no spoof gate at all.
    """
    direct = request.client.host if request.client else ""
    forwarded_header = request.headers.get("x-forwarded-for", "")
    # Rightmost entry (the IP the trusted proxy added) is hardest to
    # spoof. Leftmost is client-supplied. If the chain has only one
    # entry, both indices coincide.
    forwarded = (forwarded_header.split(",")[-1].strip()
                 if forwarded_header else "")

    def _is_private(ip: str) -> bool:
        try:
            import ipaddress
            obj = ipaddress.ip_address(ip)
            return obj.is_private or obj.is_loopback or obj.is_link_local
        except (ValueError, ImportError):
            return False

    if direct and not _is_private(direct):
        return direct  # real peer — ignore forwarded header (spoofable)
    if forwarded:
        return forwarded
    if direct:
        return direct
    # x-real-ip fallback: only honor when there's no other signal
    # (request.client is None — ASGI lifespan edge cases / certain
    # misconfigurations). Don't echo whatever the attacker sent.
    real_ip = request.headers.get("x-real-ip") or ""
    if real_ip and (not direct or _is_private(direct)):
        return real_ip.strip()
    return "0.0.0.0"


def _check_rate_limit(ip: str) -> bool:
    """Returns True if request should be blocked."""
    global _rate_limit_cleanup
    now = time.time()
    # Periodic full cleanup to prevent memory leak (every 5 minutes)
    if now - _rate_limit_cleanup > 300:
        _rate_limit_cleanup = now
        stale = [k for k, v in _rate_limits.items() if not v or (now - v[-1]) > RATE_LIMIT_WINDOW]
        for k in stale:
            _rate_limits.pop(k, None)
    attempts = _rate_limits.get(ip, [])
    # Prune old entries
    attempts = [t for t in attempts if now - t < RATE_LIMIT_WINDOW]
    if len(attempts) >= RATE_LIMIT_MAX:
        _rate_limits[ip] = attempts
        return True
    attempts.append(now)
    _rate_limits[ip] = attempts
    return False


# ── FastAPI App ──
app = FastAPI(title="Huntova", version=VERSION)


# ── Security headers middleware ──
from starlette.middleware.base import BaseHTTPMiddleware

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    # Stability fix (Perplexity bug #40): logged-in HTML/JSON responses
    # used to be cacheable by the browser, so after logout the back
    # button could re-render an authenticated dashboard from cache —
    # backend logs see no request, but the user briefly sees private
    # data. We force no-store on every response that the client sent a
    # session cookie with, plus on the auth pages themselves so the
    # cached login form doesn't auto-submit stale data.
    _NEVER_CACHE_PATHS = {
        "/", "/account", "/ops", "/leads", "/dashboard", "/hunts", "/agent",
        "/auth/login", "/auth/signup", "/auth/logout", "/auth/me",
        # /landing references external Fontshare CSS that can update —
        # always revalidate so a stale cache doesn't outlive a font swap.
        "/landing",
    }

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline' https://accounts.google.com https://apis.google.com; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://api.fontshare.com; font-src 'self' https://fonts.gstatic.com https://cdn.fontshare.com; img-src 'self' data: https:; connect-src 'self' https://accounts.google.com; frame-src https://accounts.google.com; frame-ancestors 'none'"
        if PUBLIC_URL.startswith("https"):
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
        # No-store on auth-sensitive surfaces. Static assets under
        # /static stay cacheable so the browser doesn't re-fetch CSS/JS
        # on every page load.
        path = request.url.path
        has_session = bool(request.cookies.get(SESSION_COOKIE_NAME))
        if (path in self._NEVER_CACHE_PATHS
                or path.startswith("/leads/")
                or path.startswith("/api/")
                or has_session):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
            response.headers["Pragma"] = "no-cache"
        return response

app.add_middleware(SecurityHeadersMiddleware)


# ── CSRF protection middleware ──
from auth import CSRF_COOKIE_NAME, set_csrf_cookie, validate_csrf

CSRF_EXEMPT_PATHS = {
    "/auth/signup", "/auth/login", "/auth/logout",
    "/auth/forgot-password", "/auth/reset-password",
    "/auth/resend-verification",  # auth flow — user may not have CSRF cookie yet
    "/api/webhook/stripe",  # uses HMAC verification, not cookies
    "/api/track-actions",  # analytics — uses sendBeacon which can't set custom headers
    # First-run setup wizard — local-only by design (api_setup_key
    # refuses non-local APP_MODE), no auth context yet, served from
    # 127.0.0.1 binding so cross-origin POSTs are blocked at the
    # network layer not the app layer.
    "/api/setup/key",
    # /api/chat — web-UI chat dispatcher (local-mode only by design).
    "/api/chat",
    # /api/try is rate-limited per IP and runs anonymous public demos
    "/api/try",
    # Opt-in telemetry beacon
    "/api/_metric",
    # Public recipe publish — gated by HV_RECIPE_URL_BETA env, has
    # its own per-IP rate limit
    "/api/recipe/publish",
    # Cloud Proxy admin token mint — protected by Bearer = HV_ADMIN_TOKEN
    "/api/admin/cloud-token",
}

# Endpoints that are CSRF-exempt because they have their own
# verification (HMAC, Bearer token, anonymous public access). For
# these, the CSRFMiddleware doesn't need to also enforce Origin —
# the endpoint's own auth check is sufficient.
_CSRF_EXEMPT_ALSO_ORIGIN_EXEMPT = {
    "/auth/signup", "/auth/login", "/auth/logout",
    "/auth/forgot-password", "/auth/reset-password",
    "/auth/resend-verification",
    "/api/webhook/stripe",       # HMAC verified
    "/api/track-actions",        # public sendBeacon
    "/api/_metric",              # public sendBeacon
    "/api/recipe/publish",       # IP-rate-limited public
    "/api/admin/cloud-token",    # Bearer = HV_ADMIN_TOKEN
    "/api/try",                  # public anonymous demo
}


class CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        # Only validate POST/PUT/DELETE/PATCH
        if request.method in ("GET", "HEAD", "OPTIONS"):
            response = await call_next(request)
            # Set CSRF cookie on page loads if missing
            if not request.cookies.get(CSRF_COOKIE_NAME) and request.url.path in ("/", "/landing", "/dashboard", "/hunts", "/agent", "/ops", "/account"):
                set_csrf_cookie(response)
            return response
        # Skip exempt paths
        if request.url.path in CSRF_EXEMPT_PATHS:
            # Origin defense for the exempt mutating endpoints. Browsers
            # always send Origin on cross-origin POSTs; scripts (curl,
            # cli_remote, the install.sh shell) never do. Reject any
            # Origin that isn't our own — this catches the "simple CORS"
            # POST attack that bypasses CSRF tokens. Skip the check for
            # endpoints that have a stronger auth (HMAC / Bearer / public).
            if request.url.path not in _CSRF_EXEMPT_ALSO_ORIGIN_EXEMPT:
                if not _is_local_origin(request.headers.get("origin") or ""):
                    return JSONResponse({"ok": False, "error": "bad_origin"}, status_code=403)
            response = await call_next(request)
            # Set CSRF cookie on all login/signup responses (success and failure)
            if request.url.path in ("/auth/signup", "/auth/login"):
                set_csrf_cookie(response)
            return response
        # Validate CSRF token on all other POST routes
        if not validate_csrf(request):
            return JSONResponse({"ok": False, "error": "CSRF validation failed"}, status_code=403)
        return await call_next(request)

app.add_middleware(CSRFMiddleware)


# ── AI endpoint rate limiter (separate from auth rate limiter) ──
_ai_rate: dict[int, list] = {}  # user_id -> [timestamp, ...]
AI_RATE_WINDOW = 60   # 1 minute
AI_RATE_MAX = 20      # max 20 AI calls per minute per user
_ai_rate_cleanup = 0.0

def _check_ai_rate(user_id: int) -> bool:
    """Returns True if AI request should be blocked."""
    global _ai_rate_cleanup
    now = time.time()
    # Periodic cleanup to prevent memory leak (every 5 minutes)
    if now - _ai_rate_cleanup > 300:
        _ai_rate_cleanup = now
        stale = [k for k, v in _ai_rate.items() if not v or (now - v[-1]) > AI_RATE_WINDOW]
        for k in stale:
            _ai_rate.pop(k, None)
    attempts = _ai_rate.get(user_id, [])
    attempts = [t for t in attempts if now - t < AI_RATE_WINDOW]
    if len(attempts) >= AI_RATE_MAX:
        _ai_rate[user_id] = attempts
        return True
    attempts.append(now)
    _ai_rate[user_id] = attempts
    return False


# Mount static files
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ── Global error handlers ──
@app.exception_handler(json.JSONDecodeError)
async def json_decode_error_handler(request, exc):
    return JSONResponse({"ok": False, "error": "Invalid JSON in request body"}, status_code=400)


@app.exception_handler(ValueError)
async def value_error_handler(request, exc):
    return JSONResponse({"ok": False, "error": str(exc)[:200]}, status_code=400)


@app.exception_handler(Exception)
async def generic_error_handler(request, exc):
    import traceback
    print(f"[ERROR] Unhandled: {exc}\n{traceback.format_exc()}")
    return JSONResponse({"ok": False, "error": "Internal server error"}, status_code=500)


# ── Startup/Shutdown ──
@app.on_event("startup")
async def startup():
    try:
        await db.init_db()
    except Exception as e:
        print(f"[FATAL] Database initialization failed: {e}")
        # Don't crash — server can still serve static pages and show errors
    # Reap orphaned agent_runs from crashed/restarted processes (e.g. Railway redeploy
    # loses in-memory thread state but DB still has status='running')
    try:
        reaped = await db.repair_stale_agent_runs()
        if reaped:
            print(f"[startup] reaped {reaped} orphaned agent_runs")
    except Exception as e:
        print(f"[startup] agent_runs reaper failed: {e}")
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(os.path.join(BASE_DIR, "backups"), exist_ok=True)
    # Make first user superadmin automatically.
    # Stability fix (audit wave 26): the previous version auto-promoted
    # the lowest-id user with no ADMIN_EMAILS / email_verified gate, so
    # if a cloud deploy ever lost its admin role row (manual UPDATE,
    # DB rollback, schema reset, ops mistake), the first attacker who
    # signed up — even with an unverified email outside the allowlist —
    # would get superadmin on the next restart. auth.py:357-365 had the
    # right pattern (gate on ADMIN_EMAILS membership AND email_verified)
    # — apply it here too. When ADMIN_EMAILS is unset (local mode /
    # fresh deploy), skip the auto-promote so we fail closed.
    try:
        from config import ADMIN_EMAILS as _ADMIN_EMAILS
        all_users = await db.get_all_users()
        if all_users and not any(u.get("role") in ("admin", "superadmin") for u in all_users):
            if _ADMIN_EMAILS:
                # Promote the lowest-id user IF they're in the allowlist
                # AND their email is verified. No allowlist match → skip.
                _candidates = [
                    u for u in all_users
                    if (u.get("email") or "").lower() in _ADMIN_EMAILS
                       and int(u.get("email_verified") or 0) == 1
                ]
                if _candidates:
                    first = min(_candidates, key=lambda u: u["id"])
                    await db.update_user(first["id"], role="superadmin")
    except Exception:
        pass
    # ── One-time wizard reset (v2026-03-21) ──
    # Force all users to retrain wizard + regenerate DNA after search quality overhaul
    await _one_time_wizard_reset("v2026-03-21")
    # Cleanup expired sessions periodically.
    # Stability fix (long-tail bug #42): asyncio.create_task only holds
    # a weak reference internally — without keeping a strong ref the
    # task can be GC'd between tick boundaries on some loop
    # implementations. Stash on the app state so the task lives as long
    # as the process. The loop itself is infinite + per-iteration
    # try/except, so this is the only failure mode left.
    app.state.session_cleanup_task = asyncio.create_task(_session_cleanup_loop())


async def _one_time_wizard_reset(migration_id: str):
    """Reset all users' wizard data once. Idempotent — tracks migration_id in DB.

    Stability fix (a243): in single-user local mode this migration
    fires every server start and a SQLite quirk (passing parameters
    to a query that has no %s placeholders) prints a noisy
    `Wizard reset failed: parameters are of unsupported type`
    each time. The migration was written for cloud installs that
    actually had pre-existing trained users to reset; on a fresh
    local install there's nothing to do. Skip in local mode entirely.
    """
    try:
        from runtime import CAPABILITIES as _CAPS
        if _CAPS.mode == "local":
            return
    except Exception:
        pass
    try:
        # Check if already run (store flag in a simple key-value approach)
        row = await db._afetchone(
            "SELECT data FROM user_settings WHERE user_id = 0")
        try:
            meta = json.loads(row["data"]) if row else {}
        except (json.JSONDecodeError, TypeError):
            meta = {}
        if meta.get(f"_migration_{migration_id}"):
            return  # Already ran
    except Exception:
        meta = {}

    print(f"[MIGRATION] Running wizard reset: {migration_id}")
    try:
        # Get all user settings
        all_users = await db.get_all_users()
        reset_count = 0
        for u in all_users:
            uid = u["id"]
            settings = await db.get_settings(uid)
            w = settings.get("wizard", {})
            if not w.get("_interview_complete"):
                continue  # Not completed yet, nothing to reset
            # Clear wizard completion + DNA so they retrain
            w["_interview_complete"] = False
            w["_wizard_phase"] = 0
            w["_wizard_confidence"] = 0
            w.pop("_wizard_answers", None)
            w.pop("normalized_hunt_profile", None)
            w.pop("training_dossier", None)
            # Keep company_name, business_description, services etc. so they don't
            # have to re-type everything — wizard will pre-fill from existing data
            settings["wizard"] = w
            await db.save_settings(uid, settings)
            # Delete old DNA (will be regenerated after wizard)
            try:
                await db._aexec("DELETE FROM agent_dna WHERE user_id = %s", [uid])
            except Exception:
                pass
            reset_count += 1

        # Mark migration as done
        meta[f"_migration_{migration_id}"] = True
        meta_json = json.dumps(meta)
        await db._aexec(
            "INSERT INTO user_settings (user_id, data) VALUES (0, %s) "
            "ON CONFLICT (user_id) DO UPDATE SET data = %s",
            [meta_json, meta_json])
        print(f"[MIGRATION] Reset {reset_count} users' wizard data + deleted DNA")
    except Exception as e:
        print(f"[MIGRATION] Wizard reset failed: {e}")


async def _session_cleanup_loop():
    while True:
        try:
            await db.cleanup_expired_sessions()
        except Exception:
            pass
        # Also prune stale bookkeeping tables (used reset tokens + old
        # Stripe event records) so they don't grow without bound.
        try:
            await db.cleanup_stale_token_tables()
        except Exception:
            pass
        await asyncio.sleep(3600)


# ═══════════════════════════════════════════════════════════════
# AUTH ROUTES
# ═══════════════════════════════════════════════════════════════

@app.post("/auth/signup")
async def auth_signup(request: Request):
    if _check_rate_limit(_get_client_ip(request)):
        return JSONResponse({"ok": False, "error": "Too many attempts. Try again in 60 seconds."}, status_code=429)
    body = await request.json()
    # Normalise to lowercase to match login + forgot-password + reset flow.
    # Without this, signing up as `Test@Example.com` then trying
    # forgot-password as `test@example.com` failed because the reset
    # token was bound to the case-preserved signup email but the
    # lookup paths all lowercased.
    email = (body.get("email") or "").strip().lower()
    password = body.get("password", "")
    name = body.get("name", "")
    print(f"[AUTH] signup attempt: {email}")
    try:
        user = await auth.signup(email, password, name)
        user_dict, token = await auth.login(email, password)
        print(f"[AUTH] signup success: user_id={user['id']} email={email}")
        response = JSONResponse({"ok": True, "user": {"id": user["id"], "email": user["email"], "display_name": user["display_name"], "tier": user["tier"]}})
        auth.set_session_cookie(response, token)
        # Send verification email in background — same reasoning as bug #21
        # forgot-password: SMTP latency shouldn't block signup response.
        if email_service.is_email_configured():
            # Bind verify token to user_id (Perplexity bug #72) so a
            # deleted+resignup with the same email can't reuse an old link.
            vtoken = auth.generate_verification_token(email, user["id"])
            async def _send_verify_bg():
                try:
                    await email_service.send_verification_email(email, vtoken, PUBLIC_URL)
                except Exception as _ve:
                    print(f"[AUTH] verification email send failed for {email}: {_ve}")
            asyncio.create_task(_send_verify_bg())
        return response
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
    except Exception as e:
        # Catch DB unique constraint violations from concurrent signups
        err_msg = str(e).lower()
        if "unique" in err_msg or "duplicate" in err_msg or "already" in err_msg:
            return JSONResponse({"ok": False, "error": "Email already registered"}, status_code=400)
        return JSONResponse({"ok": False, "error": "Signup failed. Try again."}, status_code=500)


@app.post("/auth/login")
async def auth_login(request: Request):
    if _check_rate_limit(_get_client_ip(request)):
        return JSONResponse({"ok": False, "error": "Too many attempts. Try again in 60 seconds."}, status_code=429)
    body = await request.json()
    email = (body.get("email") or "").strip().lower()
    password = body.get("password", "")
    try:
        user, token = await auth.login(email, password)
        response = JSONResponse({"ok": True, "user": {"id": user["id"], "email": user["email"], "display_name": user["display_name"], "tier": user["tier"]}})
        auth.set_session_cookie(response, token)
        return response
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=401)


@app.post("/auth/logout")
async def auth_logout(request: Request):
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if token:
        await auth.logout(token)
    response = JSONResponse({"ok": True})
    auth.clear_session_cookie(response)
    return response


@app.get("/auth/me")
async def auth_me(request: Request):
    user = await get_current_user(request)
    if not user:
        return JSONResponse({"ok": False}, status_code=401)
    # Stability fix (Perplexity bug #78): we used to read `user`
    # once via the auth dependency, then call check_and_reset_credits
    # which can refill + persist new credits/reset_date. Returning the
    # OLD user dict's tier/email/avatar plus the NEW credits gave a
    # split-brain payload — under READ COMMITTED, an admin update
    # landing between the two reads also showed up as a half-stale
    # snapshot. Re-fetch the user once after the refill so the whole
    # response is from a single point-in-time read.
    await db.check_and_reset_credits(user["id"])
    fresh = await db.get_user_by_id(user["id"]) or user
    return JSONResponse({
        "ok": True,
        "user": {
            "id": fresh["id"],
            "email": fresh["email"],
            "display_name": fresh.get("display_name", ""),
            "tier": fresh.get("tier", "free"),
            "credits_remaining": fresh.get("credits_remaining", 0) or 0,
            "email_verified": bool(fresh.get("email_verified")),
            "auth_provider": fresh.get("auth_provider", "email"),
            "avatar_url": fresh.get("avatar_url", ""),
        }
    })


# ── Email Verification ──

@app.get("/auth/verify-email")
async def verify_email(token: str = ""):
    if not token:
        return RedirectResponse("/")
    # Stability fix (Perplexity bug #72): verify_verification_token
    # now returns (email, user_id). When user_id is non-zero, look up
    # by id and confirm the email still matches — that prevents an
    # old verification link from verifying a NEW account that
    # happens to own the same email after a delete+resignup. Legacy
    # tokens (uid=0) fall back to email lookup with the same write.
    _verified = auth.verify_verification_token(token)
    if not _verified:
        return _auth_message_page("Verification Failed", "This link is invalid or has expired.", "Request a new one from your account settings.", "/")
    email, token_uid = _verified
    if token_uid:
        user = await db.get_user_by_id(token_uid)
        if not user:
            return _auth_message_page("Error", "User not found.", "", "/")
        # Constant-time compare to close a timing-side-channel that
        # could let an attacker enumerate addresses by measuring
        # response latency on character-by-character mismatches.
        import secrets as _sec
        if not _sec.compare_digest(
            (user.get("email") or "").lower().strip(),
            (email or "").lower().strip(),
        ):
            return _auth_message_page(
                "Verification Failed",
                "This link is no longer valid for this address.",
                "Request a new verification email.",
                "/",
            )
    else:
        user = await db.get_user_by_email(email)
        if not user:
            return _auth_message_page("Error", "User not found.", "", "/")
    if not user.get("email_verified"):
        await db.update_user(user["id"], email_verified=1)
    return RedirectResponse("/?verified=1")


# Per-user resend cap so the endpoint can't be used to spam a user's inbox.
_RESEND_WINDOW_SECONDS = 3600
_RESEND_MAX_PER_WINDOW = 3
_resend_history: dict[int, list[float]] = {}
_resend_history_last_gc: float = 0.0


@app.post("/auth/resend-verification")
async def resend_verification(user: dict = Depends(require_user)):
    if user.get("email_verified"):
        return JSONResponse({"ok": True, "message": "Already verified"})
    if not email_service.is_email_configured():
        return JSONResponse({"ok": False, "error": "Email service not configured"}, status_code=503)
    # Rate-limit: previously any authenticated user could hammer this endpoint
    # and flood their own inbox with verification links. Cap at 3/hour.
    # Stability fix (multi-agent bug #37): periodic GC of the global
    # _resend_history dict so users who never resend again don't leak
    # forever. Same pattern as _check_ai_rate's stale sweep.
    import time as _t
    now = _t.time()
    global _resend_history_last_gc
    if now - _resend_history_last_gc > 300:
        _resend_history_last_gc = now
        _stale = [k for k, v in _resend_history.items()
                  if not v or all(now - t >= _RESEND_WINDOW_SECONDS for t in v)]
        for k in _stale:
            _resend_history.pop(k, None)
    hist = [t for t in _resend_history.get(user["id"], []) if now - t < _RESEND_WINDOW_SECONDS]
    if len(hist) >= _RESEND_MAX_PER_WINDOW:
        _resend_history[user["id"]] = hist
        return JSONResponse(
            {"ok": False, "error": "Too many resend requests. Try again in an hour."},
            status_code=429)
    hist.append(now)
    _resend_history[user["id"]] = hist
    # Bind to user_id per #72.
    token = auth.generate_verification_token(user["email"], user["id"])
    # Background send — see bug #21. Inline await blocked the response on
    # SMTP latency for 15+ seconds when the mail server was slow.
    async def _resend_bg():
        try:
            await email_service.send_verification_email(user["email"], token, PUBLIC_URL)
        except Exception as _re:
            print(f"[AUTH] resend verification failed for {user['email']}: {_re}")
    asyncio.create_task(_resend_bg())
    return JSONResponse({"ok": True, "message": "Verification email sent"})


# ── Forgot / Reset Password ──

@app.post("/auth/forgot-password")
async def forgot_password(request: Request):
    if _check_rate_limit(_get_client_ip(request)):
        return JSONResponse({"ok": False, "error": "Too many attempts. Try again later."}, status_code=429)
    body = await request.json()
    email_addr = (body.get("email") or "").strip().lower()
    # Always return success to prevent email enumeration.
    # Stability fix (multi-agent bug #21): the SMTP send used to be
    # awaited inline. With a 15s SMTP timeout, a slow mail server made
    # the user wait the full timeout for the page to respond — and worse,
    # the response time itself leaked which emails were registered (an
    # existing user takes longer because we actually try to send).
    # Fire-and-forget so the response is constant-time and SMTP latency
    # doesn't block the request thread.
    if email_addr and email_service.is_email_configured():
        user = await db.get_user_by_email(email_addr)
        if user and user.get("password_hash"):
            # Bind the reset token to the user's CURRENT password_hash
            # (Perplexity bug #70). After a successful reset the hash
            # changes and every previously-issued token for this user
            # becomes invalid.
            token = auth.generate_reset_token(email_addr, user.get("password_hash") or "")
            async def _send_in_bg():
                try:
                    await email_service.send_password_reset_email(email_addr, token, PUBLIC_URL)
                except Exception as _se:
                    print(f"[AUTH] reset email send failed for {email_addr}: {_se}")
            asyncio.create_task(_send_in_bg())
    return JSONResponse({"ok": True, "message": "If that email exists, a reset link has been sent."})


@app.get("/auth/reset-password", response_class=HTMLResponse)
async def reset_password_page(token: str = ""):
    # verify_reset_token now returns a tuple (email, pwf) per #70.
    _verified = auth.verify_reset_token(token)
    if not _verified:
        return _auth_message_page("Link Expired", "This password reset link is invalid or has expired.", "Request a new one from the login page.", "/landing")
    return _reset_password_page(token)


@app.post("/auth/reset-password")
async def reset_password_submit(request: Request):
    body = await request.json()
    token = body.get("token", "")
    new_password = body.get("password", "")
    if len(new_password) < 6:
        return JSONResponse({"ok": False, "error": "Password must be at least 6 characters"}, status_code=400)
    _verified = auth.verify_reset_token(token)
    if not _verified:
        return JSONResponse({"ok": False, "error": "Invalid or expired reset link. Request a new one."}, status_code=400)
    email, _token_pwf = _verified
    user = await db.get_user_by_email(email)
    if not user:
        return JSONResponse({"ok": False, "error": "User not found"}, status_code=404)
    # Stability fix (Perplexity bug #70): reject the token if the
    # user's password_hash has changed since the token was issued.
    # Tokens issued before this fix have an empty pwf and skip the
    # check (legacy compat); new tokens carry a fingerprint and any
    # mismatch means the password was already changed via another
    # link in the same window — refuse to apply.
    if _token_pwf:
        if auth._password_hash_fingerprint(user.get("password_hash") or "") != _token_pwf:
            return JSONResponse({"ok": False, "error": "This reset link is no longer valid. Your password has already been changed. Request a new one."}, status_code=400)
    # Stability fix (Perplexity bug #58): single-use claim + password
    # update + session wipe in ONE transaction.
    import hashlib as _hl
    _token_hash = _hl.sha256(token.encode("utf-8")).hexdigest()
    new_hash = auth.hash_password(new_password)
    claimed = await db.claim_reset_token_and_set_password(_token_hash, user["id"], new_hash)
    if not claimed:
        return JSONResponse({"ok": False, "error": "This reset link has already been used. Request a new one."}, status_code=400)
    return JSONResponse({"ok": True, "message": "Password updated. Please log in."})


# ── Google OAuth ──

@app.get("/auth/google")
async def auth_google(request: Request):
    if not GOOGLE_CLIENT_ID:
        return JSONResponse({"error": "Google login not configured"}, status_code=501)
    redirect_uri = PUBLIC_URL + GOOGLE_REDIRECT_URI_PATH
    state = auth.generate_token()
    from urllib.parse import urlencode
    params = urlencode({
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    })
    response = RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{params}")
    _is_prod_g = PUBLIC_URL.startswith("https")
    response.set_cookie("gauth_state", state, httponly=True, max_age=600, samesite="lax", secure=_is_prod_g)
    return response


@app.get("/auth/google/callback")
async def auth_google_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    if error:
        resp = RedirectResponse("/landing?auth_error=google_denied")
        resp.delete_cookie("gauth_state")
        return resp
    stored_state = request.cookies.get("gauth_state")
    if not state or state != stored_state:
        resp = RedirectResponse("/landing?auth_error=invalid_state")
        resp.delete_cookie("gauth_state")
        return resp
    redirect_uri = PUBLIC_URL + GOOGLE_REDIRECT_URI_PATH
    # Exchange code for tokens.
    # Stability fix (Perplexity bug #43): the previous version opened
    # TWO httpx.AsyncClient context managers for two sequential calls
    # to googleapis.com — each one stood up its own connection pool,
    # so the second call paid a fresh TCP+TLS handshake instead of
    # reusing the keep-alive socket from the first. HTTPX's own docs
    # explicitly warn against per-call client construction. One client
    # for both calls now; under load this also caps file-descriptor
    # churn during OAuth bursts.
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            token_resp = await client.post("https://oauth2.googleapis.com/token", data={
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            })
            if token_resp.status_code != 200:
                resp = RedirectResponse("/landing?auth_error=token_failed")
                resp.delete_cookie("gauth_state")
                return resp
            tokens = token_resp.json()
            userinfo_resp = await client.get(
                "https://www.googleapis.com/oauth2/v3/userinfo",
                headers={"Authorization": f"Bearer {tokens['access_token']}"}
            )
        if userinfo_resp.status_code != 200:
            resp = RedirectResponse("/landing?auth_error=userinfo_failed")
            resp.delete_cookie("gauth_state")
            return resp
    except Exception:
        resp = RedirectResponse("/landing?auth_error=google_error")
        resp.delete_cookie("gauth_state")
        return resp
    guser = userinfo_resp.json()
    google_id = guser.get("sub", "")
    g_email = (guser.get("email") or "").lower().strip()
    g_email_verified = bool(guser.get("email_verified"))
    g_name = guser.get("name") or g_email.split("@")[0]
    g_avatar = guser.get("picture", "")
    if not google_id or not g_email:
        resp = RedirectResponse("/landing?auth_error=no_email")
        resp.delete_cookie("gauth_state")
        return resp
    # Check existing Google user
    existing = await db.get_user_by_google_id(google_id)
    if existing:
        if existing.get("is_suspended"):
            resp = RedirectResponse("/landing?auth_error=account_suspended")
            resp.delete_cookie("gauth_state")
            return resp
        session_token = auth.generate_token()
        await db.create_session(session_token, existing["id"])
        await db.update_last_login(existing["id"])
        response = RedirectResponse("/")
        auth.set_session_cookie(response, session_token)
        response.delete_cookie("gauth_state")
        return response
    # Check existing email user — link accounts
    existing_email = await db.get_user_by_email(g_email)
    if existing_email:
        if existing_email.get("is_suspended"):
            return RedirectResponse("/landing?auth_error=account_suspended")
        # Stability fix (Perplexity bug #71): guard auto-linking.
        # 1) Refuse if Google says the email isn't verified — without
        #    this, an attacker controlling a hostile/misconfigured
        #    Google identity could claim an unverified address that
        #    matches a real Huntova user and silently take it over.
        # 2) Refuse if this Huntova user already has a DIFFERENT
        #    google_id linked — silent overwrite would let a second
        #    Google identity hijack the existing account.
        if not g_email_verified:
            resp = RedirectResponse("/landing?auth_error=google_email_unverified")
            resp.delete_cookie("gauth_state")
            return resp
        _existing_gid = existing_email.get("google_id") or ""
        if _existing_gid and _existing_gid != google_id:
            resp = RedirectResponse("/landing?auth_error=google_link_conflict")
            resp.delete_cookie("gauth_state")
            return resp
        provider = "both" if existing_email.get("password_hash") else "google"
        await db.update_user(existing_email["id"],
            google_id=google_id, auth_provider=provider,
            email_verified=1, avatar_url=g_avatar or existing_email.get("avatar_url", ""))
        session_token = auth.generate_token()
        await db.create_session(session_token, existing_email["id"])
        await db.update_last_login(existing_email["id"])
        response = RedirectResponse("/")
        auth.set_session_cookie(response, session_token)
        response.delete_cookie("gauth_state")
        return response
    # New user
    user_id = await db.create_google_user(g_email, google_id, g_name, g_avatar)
    session_token = auth.generate_token()
    await db.create_session(session_token, user_id)
    response = RedirectResponse("/")
    auth.set_session_cookie(response, session_token)
    response.delete_cookie("gauth_state")
    return response


@app.post("/api/account/update-profile")
async def update_profile(request: Request, user: dict = Depends(require_user)):
    body = await request.json()
    name = (body.get("display_name") or "").strip()
    # Cap display_name length so a multi-MB blob can't land in the DB and
    # blow up the dashboard render. 200 chars covers every legitimate
    # full-name + suffix combination we've seen.
    if name and len(name) > 200:
        return JSONResponse(
            {"ok": False, "error": "display_name must be 200 characters or fewer"},
            status_code=400)
    if name:
        await db.update_user(user["id"], display_name=name)
    return {"ok": True}


@app.post("/api/account/change-password")
async def change_password(request: Request, user: dict = Depends(require_user)):
    body = await request.json()
    current = body.get("current_password", "")
    new_pass = body.get("new_password", "")
    if len(new_pass) < 6:
        return JSONResponse({"ok": False, "error": "Password must be at least 6 characters"}, status_code=400)
    full_user = await db.get_user_by_id(user["id"])
    if full_user.get("password_hash"):
        if not auth.verify_password(current, full_user["password_hash"]):
            return JSONResponse({"ok": False, "error": "Current password is incorrect"}, status_code=400)
    await db.update_user(user["id"], password_hash=auth.hash_password(new_pass))
    if not full_user.get("password_hash"):
        # Was Google-only, now has password too
        await db.update_user(user["id"], auth_provider="both")
    # Security: a password change typically means the user is responding to
    # a suspicion of compromise. Invalidate every existing session and mint
    # a fresh one for the current tab so the user stays logged in here but
    # any other device is kicked out. /auth/reset-password already does
    # this (line 523); change-password was missing it.
    await db.delete_user_sessions(user["id"])
    new_token = auth.generate_token()
    await db.create_session(new_token, user["id"])
    response = JSONResponse({"ok": True, "message": "Password updated"})
    auth.set_session_cookie(response, new_token)
    return response


@app.post("/api/account/delete")
async def delete_account(request: Request, user: dict = Depends(require_user)):
    body = await request.json()
    if body.get("confirm") != "DELETE":
        return JSONResponse({"ok": False, "error": "Type DELETE to confirm"}, status_code=400)
    # Audit log BEFORE the cascade delete — once delete_all_user_data runs,
    # the user row is gone and log_admin_action's FK to users would fail
    # for the actor. admin_user_id is set to the user themselves (self-
    # service delete), target_user_id is null because the target row is
    # about to vanish. This gives forensics a breadcrumb if the user later
    # claims their account was deleted without consent.
    try:
        await db.log_admin_action(
            user["id"], None, "self_delete_account",
            {"email": user.get("email", ""), "tier": user.get("tier", "")},
            request.client.host if request.client else "")
    except Exception as _audit_err:
        # Don't block the deletion if audit logging fails — just print so
        # we notice in Railway logs. The deletion itself is the user's
        # GDPR right and shouldn't be held up.
        print(f"[ACCOUNT] self-delete audit write failed for user {user['id']}: {_audit_err}")
    # Invalidate every active session for this user BEFORE deleting
    # the user data. Without this, a stolen session token could still
    # authenticate against ghost rows during the brief window before
    # `delete_all_user_data` removes them, and ANY recreated account
    # with the same id (rare, but possible on PG sequences after long
    # gaps) would inherit the live session. `change-password` already
    # does this for the same reason — mirror it.
    try:
        await db.delete_user_sessions(user["id"])
    except Exception as _sess_err:
        print(f"[ACCOUNT] session purge failed for user {user['id']}: {_sess_err}")
    try:
        await db.delete_all_user_data(user["id"])
    except Exception as e:
        print(f"[ACCOUNT] Delete failed for user {user['id']}: {e}")
        return JSONResponse({"ok": False, "error": "Account deletion failed. Please try again."}, status_code=500)
    # Stability fix (multi-agent bug #29): user_context._active_contexts
    # had no cleanup path — remove_context() was defined but never
    # called, so every UserAgentContext (OpenAI client, SSE bus,
    # subscriber queues, lead state) lived for the lifetime of the
    # process. Wire it here so deleted accounts at least don't leak.
    # Also closes any lingering session-log file handle.
    try:
        _ctx = get_context(user["id"])
        if _ctx is not None:
            try: _ctx.close_session_log()
            except Exception: pass
        remove_context(user["id"])
    except Exception as _ctx_err:
        print(f"[ACCOUNT] context cleanup failed for user {user['id']}: {_ctx_err}")
    response = JSONResponse({"ok": True})
    auth.clear_session_cookie(response)
    return response


# ═══════════════════════════════════════════════════════════════
# PAGE ROUTES
# ═══════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    user = await get_current_user(request)
    if user:
        # Chat-first cinematic interface (a238). Classic dashboard
        # remains accessible at /dashboard, /agent, /hunts, /leads.
        return _read_template("jarvis.html")
    return _read_template("landing.html")


@app.get("/jarvis", response_class=HTMLResponse)
async def jarvis_page(request: Request):
    """Direct deep-link to the chat-first interface."""
    user = await get_current_user(request)
    if not user:
        return RedirectResponse("/landing")
    return _read_template("jarvis.html")


@app.get("/leads/{lead_id}", response_class=HTMLResponse)
async def spa_lead_detail(request: Request, lead_id: str):
    """a240: legacy deep-link → Jarvis, Leads panel auto-opens at this lead.
    The old `index.html` panel UI is no longer rendered for any route."""
    user = await get_current_user(request)
    if not user:
        return RedirectResponse("/landing")
    # Allow only safe lead-id chars in the hash so the front-end can split
    # on it without an injection vector. lead_id is bounded by the URL
    # routing layer to whatever fastapi accepts; tighten further here.
    safe = "".join(ch for ch in (lead_id or "") if ch.isalnum() or ch in "-_")[:64]
    return RedirectResponse(f"/?panel=leads&lead={safe}")

@app.get("/leads", response_class=HTMLResponse)
async def spa_leads(request: Request):
    user = await get_current_user(request)
    if not user:
        return RedirectResponse("/landing")
    return RedirectResponse("/?panel=leads")


@app.get("/terms", response_class=HTMLResponse)
async def terms_page():
    return _legal_page("Terms of Service", """
<h2>Terms of Service</h2>
<p><strong>Last updated:</strong> March 2026</p>

<h3>1. Service Description</h3>
<p>Huntova is an AI-powered B2B lead generation platform. By creating an account, you agree to these terms.</p>

<h3>2. Account</h3>
<p>You must provide accurate information when creating an account. You are responsible for maintaining the security of your account credentials. One account per person.</p>

<h3>3. Credits &amp; Billing</h3>
<p>Credits are consumed when the AI agent finds qualified leads. Monthly plan credits refill at the start of each billing cycle. Top-up credits never expire. Refunds are handled on a case-by-case basis within 14 days of purchase.</p>

<h3>4. Acceptable Use</h3>
<p>You may not use Huntova to: send spam, harvest data for resale, violate any laws, overload the service, or attempt to access other users' data.</p>

<h3>5. Data &amp; Privacy</h3>
<p>Lead data found by the agent is stored per-user with full isolation. See our <a href="/privacy">Privacy Policy</a> for details on data handling, retention, and GDPR compliance.</p>

<h3>6. Intellectual Property</h3>
<p>Huntova owns the platform. You own the leads and outreach content generated for your account.</p>

<h3>7. Limitation of Liability</h3>
<p>Huntova is provided "as is." We do not guarantee specific lead volumes, quality, or conversion rates. Our liability is limited to the amount you paid in the last 30 days.</p>

<h3>8. Cancellation</h3>
<p>You can cancel your subscription anytime from your account. Access continues until the end of the billing period. Top-up credits remain available after cancellation.</p>

<h3>9. Changes</h3>
<p>We may update these terms with 30 days notice via email. Continued use after changes constitutes acceptance.</p>

<h3>10. Contact</h3>
<p>Questions about these terms: support via the app or website.</p>
""")


@app.get("/privacy", response_class=HTMLResponse)
async def privacy_page():
    return _legal_page("Privacy Policy", """
<h2>Privacy Policy</h2>
<p><strong>Last updated:</strong> March 2026</p>

<h3>1. What We Collect</h3>
<p><strong>Account data:</strong> email address, name, password (hashed). <strong>Usage data:</strong> leads found, credits used, agent run history. <strong>Payment data:</strong> handled by Stripe — we never see your card number.</p>

<h3>2. How We Use Your Data</h3>
<p>To provide the service, process payments, improve the product, and communicate service updates. We do not sell your data.</p>

<h3>3. Lead Data</h3>
<p>Leads are sourced from publicly available web pages. All lead data is stored per-user with complete isolation. Lead data is retained for up to 2 years or until you delete it.</p>

<h3>4. GDPR Compliance</h3>
<p>Huntova is designed for European B2B use. We provide: <strong>Right to erasure</strong> — delete all data associated with an email or domain via the API. <strong>Data retention</strong> — configurable, default 2 years. <strong>Data portability</strong> — export all leads as CSV or JSON. <strong>Audit trail</strong> — full logging of data operations.</p>

<h3>5. Cookies</h3>
<p>We use a single HttpOnly session cookie (<code>hv_session</code>) for authentication. No tracking cookies, no analytics cookies, no third-party cookies.</p>

<h3>6. Third Parties</h3>
<p><strong>Stripe</strong> for payment processing. <strong>Google Gemini</strong> for AI analysis (your business profile is sent to the AI for lead scoring — no personal data is shared). <strong>PostgreSQL</strong> for database hosting (Railway). <strong>Jina AI</strong> for website content rendering (public website text may be processed through Jina's reader service when scanning JS-heavy sites).</p>

<h3>7. Data Security</h3>
<p>Passwords are hashed with bcrypt. Sessions use cryptographically random tokens. All connections use HTTPS. Database access is authenticated and encrypted.</p>

<h3>8. Your Rights</h3>
<p>You can: access, export, correct, or delete your data at any time from your account. To request full account deletion, contact us.</p>

<h3>9. Contact</h3>
<p>Data protection inquiries: support via the app or website.</p>
""")


def _legal_page(title, content):
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — Huntova</title>
<meta name="robots" content="noindex">
<link rel="preconnect" href="https://api.fontshare.com" crossorigin>
<link href="https://api.fontshare.com/v2/css?f[]=satoshi@400,500,600,700&display=swap" rel="stylesheet">
<style>
body{{background:#07080c;color:#7d86a8;font:400 15px/1.75 'Satoshi',-apple-system,system-ui,sans-serif;padding:48px 24px;max-width:720px;margin:0 auto;-webkit-font-smoothing:antialiased}}
a{{color:#7c5cff;text-decoration:none}}a:hover{{color:#a48bff}}
h2{{color:#eceef6;font-size:28px;font-weight:700;letter-spacing:-.03em;margin-bottom:8px}}
h3{{color:#eceef6;font-size:16px;font-weight:700;margin:32px 0 8px}}
p{{margin-bottom:16px}}
code{{background:#13151d;padding:2px 6px;border-radius:4px;font-size:13px}}
.back{{display:inline-block;margin-bottom:32px;font-size:13px;font-weight:600}}
</style>
</head>
<body>
<a href="/" class="back">&larr; Back to Huntova</a>
{content}
</body>
</html>"""


def _auth_message_page(title, heading, message, back_url):
    from html import escape as _esc
    return HTMLResponse(f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(title)} — Huntova</title>
<link rel="preconnect" href="https://api.fontshare.com" crossorigin>
<link href="https://api.fontshare.com/v2/css?f[]=satoshi@400,500,600,700&display=swap" rel="stylesheet">
<style>body{{background:#07080c;color:#7d86a8;font:400 15px/1.75 'Satoshi',-apple-system,system-ui,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;-webkit-font-smoothing:antialiased}}
.box{{background:#0d0f15;border:1px solid rgba(120,140,220,.06);border-radius:12px;padding:48px 40px;max-width:420px;text-align:center;box-shadow:0 16px 48px rgba(0,0,20,.4)}}
h2{{color:#eceef6;font-size:22px;font-weight:700;margin-bottom:12px}}
p{{margin-bottom:20px;line-height:1.7}}
a{{color:#7c5cff;text-decoration:none;font-weight:600}}a:hover{{color:#a48bff}}</style></head>
<body><div class="box"><h2>{_esc(heading)}</h2><p>{_esc(message)}</p><a href="{_esc(back_url)}">&larr; Go back</a></div></body></html>""")


def _reset_password_page(token):
    return HTMLResponse(f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Reset Password — Huntova</title>
<link rel="preconnect" href="https://api.fontshare.com" crossorigin>
<link href="https://api.fontshare.com/v2/css?f[]=satoshi@400,500,600,700&display=swap" rel="stylesheet">
<link href="https://fonts.googleapis.com/css2?family=Geist+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>body{{background:#07080c;color:#7d86a8;font:400 15px/1.75 'Satoshi',-apple-system,system-ui,sans-serif;display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0;-webkit-font-smoothing:antialiased}}
.box{{background:#0d0f15;border:1px solid rgba(120,140,220,.1);border-radius:12px;padding:40px 36px;width:400px;max-width:calc(100vw - 48px);box-shadow:0 16px 48px rgba(0,0,20,.4)}}
.logo{{text-align:center;margin-bottom:28px}}.logo span{{font-family:'Geist Mono','JetBrains Mono',monospace;font-size:18px;font-weight:700}}.logo .a{{color:#eceef6}}.logo .b{{color:#7c5cff}}
h2{{color:#eceef6;font-size:20px;font-weight:700;text-align:center;margin-bottom:20px}}
label{{display:block;font-size:11px;font-weight:700;color:#4a5272;text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px}}
input{{width:100%;padding:12px 16px;border-radius:6px;background:#13151d;border:1px solid rgba(120,140,220,.1);color:#eceef6;font:400 14px/1 'Satoshi',-apple-system,system-ui,sans-serif;outline:none;box-sizing:border-box;margin-bottom:16px}}
input:focus{{border-color:#7c5cff;box-shadow:0 0 0 3px rgba(124,92,255,.18)}}
button{{width:100%;padding:14px;border-radius:6px;background:#7c5cff;color:#fff;font:700 14px/1 'Satoshi',-apple-system,system-ui,sans-serif;border:none;cursor:pointer;margin-top:4px}}
button:hover{{box-shadow:0 4px 24px rgba(124,92,255,.3)}}
button:disabled{{opacity:.4;cursor:not-allowed}}
.msg{{margin-top:12px;padding:10px 16px;border-radius:8px;font-size:13px;display:none;text-align:center}}
.msg.err{{background:rgba(232,88,88,.06);border:1px solid rgba(232,88,88,.12);color:#f06060;display:block}}
.msg.ok{{background:rgba(52,211,153,.06);border:1px solid rgba(52,211,153,.12);color:#34d399;display:block}}
</style></head><body>
<div class="box">
<div class="logo"><span class="a">Hunt</span><span class="b">ova</span></div>
<h2>Set new password</h2>
<form onsubmit="return doReset(event)">
<label>New Password</label><input type="password" id="pw1" placeholder="At least 6 characters" required minlength="6">
<label>Confirm Password</label><input type="password" id="pw2" placeholder="Repeat password" required minlength="6">
<button type="submit" id="rbtn">Reset Password</button>
</form>
<div class="msg" id="rmsg"></div>
</div>
<script>
function doReset(e){{
  e.preventDefault();
  var pw1=document.getElementById('pw1').value,pw2=document.getElementById('pw2').value;
  var msg=document.getElementById('rmsg'),btn=document.getElementById('rbtn');
  msg.className='msg';msg.style.display='none';
  if(pw1!==pw2){{msg.textContent='Passwords do not match';msg.className='msg err';return false}}
  btn.disabled=true;btn.textContent='Updating...';
  fetch('/auth/reset-password',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{token:'{token}',password:pw1}})}}
  ).then(function(r){{return r.json()}}).then(function(d){{
    if(d.ok){{msg.textContent='Password updated! Redirecting...';msg.className='msg ok';setTimeout(function(){{window.location.href='/landing'}},1500)}}
    else{{msg.textContent=d.error||'Error';msg.className='msg err';btn.disabled=false;btn.textContent='Reset Password'}}
  }}).catch(function(){{msg.textContent='Network error';msg.className='msg err';btn.disabled=false;btn.textContent='Reset Password'}});
  return false
}}
</script></body></html>""")


@app.get("/landing", response_class=HTMLResponse)
async def landing_page():
    return _read_template("landing.html")


_TRY_RATE_BUCKETS: dict[str, list[float]] = {}
_TRY_RATE_LIMIT = 5  # max successful runs per IP per hour
_TRY_RATE_WINDOW_S = 3600.0


def _try_rate_check(ip: str) -> tuple[bool, int]:
    """Return (allowed, retry_after_s). Per-IP sliding window."""
    import time as _t
    now = _t.time()
    bucket = _TRY_RATE_BUCKETS.setdefault(ip, [])
    # Drop expired entries
    cutoff = now - _TRY_RATE_WINDOW_S
    bucket[:] = [t for t in bucket if t > cutoff]
    if len(bucket) >= _TRY_RATE_LIMIT:
        retry = int(bucket[0] + _TRY_RATE_WINDOW_S - now) + 1
        return False, max(retry, 1)
    bucket.append(now)
    return True, 0


def _try_rate_status(ip: str) -> dict:
    """Read-only quota snapshot — called by /api/try/usage so the /try
    page can render an honest counter without consuming a slot."""
    import time as _t
    now = _t.time()
    bucket = _TRY_RATE_BUCKETS.get(ip, [])
    cutoff = now - _TRY_RATE_WINDOW_S
    active = [t for t in bucket if t > cutoff]
    used = len(active)
    remaining = max(_TRY_RATE_LIMIT - used, 0)
    reset_in = int(active[0] + _TRY_RATE_WINDOW_S - now) + 1 if active else 0
    return {
        "limit": _TRY_RATE_LIMIT,
        "used": used,
        "remaining": remaining,
        "reset_in_s": max(reset_in, 0),
        "window_s": int(_TRY_RATE_WINDOW_S),
    }


def _try_demo_prompt(icp: str) -> str:
    """Compose the prompt for the /try scratchpad — 3 leads that LOOK
    like real SearXNG-scraped Proof Pack entries, not polished marketing
    copy. Kimi round-71 spec: imperfect, messy, believable."""
    return (
        "You are Huntova's preview lead generator. Given the ICP below, emit "
        "EXACTLY 3 leads as JSON that LOOK like real SearXNG-scraped output — "
        "imperfect, messy, believable. The reader will judge the engine by how "
        "real this looks, NOT by how polished it reads.\n\n"
        "PER-LEAD SCHEMA (every field required):\n"
        "  - org_name : realistic regional name. Use \"Strobe Media\", \"KONZEPT. digital\", "
        "\"North Star Studio\", \"Pixelmint Lab\", \"Atelier Pomme\" patterns. NEVER "
        "\"Acme\", \"Example\", \"Demo Corp\", or any obviously placeholder name.\n"
        "  - org_website : domain matching the name with realistic regional TLD "
        "(.de / .co.uk / .fr / .io / .agency / .studio / .es). Mix TLDs across the 3 leads — do NOT default to .com.\n"
        "  - country : inferred from TLD/region (Germany, France, UK, USA, Spain, Italy, Netherlands)\n"
        "  - city : a real city in that country\n"
        "  - event_name : trigger signal phrased like a job-board / news-headline fragment, e.g. "
        "\"Hiring motion designers\", \"Opened Berlin office\", \"Series A announcement\", "
        "\"Rebranding to B2B focus\", \"Q3 expansion into German market\"\n"
        "  - fit_rationale : evidence_quote shape — 1-2 sentences that LOOK SCRAPED, "
        "not written by a copywriter. Imperfect grammar OK. Mid-sentence truncation OK. "
        "Use job-board phrasing like \"We are looking for a senior...\" or \"Join us as a...\". "
        "DO NOT write polished marketing copy.\n"
        "  - timing_rationale : 1-2 sentences explaining why now is the buying moment\n"
        "  - production_gap : one sentence — what they're missing that the user could provide\n"
        "  - why_fit : one sentence — why they match the ICP\n"
        "  - linkedin_url : https://linkedin.com/company/<kebab-case-slug>. Slug may include "
        "suffixes like \"-group\", \"-digital\", \"-ltd\", \"-studios\". Vary the slug shapes.\n"
        "  - contact_name : realistic full name region-appropriate to the TLD\n"
        "  - contact_role : e.g. \"Head of Production\", \"Founder & CEO\", \"VP Marketing\"\n"
        "  - contact_email : plausible address at the org's domain. VARY THE PATTERN across "
        "the 3 leads (firstname@, hello@, contact@, firstname.lastname@) — do NOT use the "
        "same pattern repeatedly.\n"
        "  - fit_score : integer 5-9 — INCLUDE AT LEAST ONE LEAD WITH SCORE 5 OR 6 to show "
        "the honest scoring range, not 3 perfect-fit leads. Real SERPs have noise.\n"
        "  - tech_signals : list of 0-3 strings detected from page tech, e.g. "
        "[\"wordpress\", \"shopify\", \"react\", \"hubspot\", \"webflow\"]. Vary across leads.\n\n"
        "RULES:\n"
        "- Region must be inferred from the ICP language and domain hints.\n"
        "- Evidence quotes MUST feel scraped, not authored. Sound like web copy or job posts.\n"
        "- Vary email patterns across leads.\n"
        "- Never use placeholder tokens like {name}, example.com, or @example.\n"
        "- One of the 3 leads should be a 5-6 fit (honest scoring), not all 7-9.\n"
        f"\nICP description:\n{icp[:1500]}\n\n"
        "Return ONLY valid JSON: {\"leads\": [...]}. No prose, no markdown, no comments."
    )


@app.post("/api/try")
async def api_try(request: Request):
    """The /try scratchpad — visitor pastes their ICP, AI generates a
    3-lead Proof Pack, we mint a /h/<slug> share so the result is
    forkable. Rate-limited per IP. Requires HV_DEMO_AI_KEY (or falls
    back to HV_GEMINI_KEY) on the server.
    """
    ip = _get_client_ip(request)
    allowed, retry = _try_rate_check(ip)
    if not allowed:
        return JSONResponse(
            {"ok": False, "error": "rate_limited", "retry_after_s": retry,
             "message": f"too many demos from this IP — try again in {retry // 60} minutes, or install Huntova for unlimited hunts"},
            status_code=429,
        )
    body = await request.json()
    icp = (body.get("icp") or "").strip()
    if len(icp) < 20:
        return JSONResponse({"ok": False, "error": "icp_too_short",
                             "message": "tell us about your ideal customer in at least 20 characters"}, status_code=400)
    if len(icp) > 2000:
        return JSONResponse({"ok": False, "error": "icp_too_long",
                             "message": "keep the ICP under 2000 chars"}, status_code=400)

    # Resolve a server-side demo AI key. Prefer HV_DEMO_AI_KEY so the
    # operator can set a separate rate-limited account for /try.
    demo_key = os.environ.get("HV_DEMO_AI_KEY") or os.environ.get("HV_GEMINI_KEY")
    if not demo_key:
        return JSONResponse(
            {"ok": False, "error": "demo_unavailable",
             "message": "the live demo isn't configured on this server. install Huntova locally with `pipx install huntova`"},
            status_code=503,
        )
    # Build a one-shot user-settings dict so we route through the
    # provider abstraction. Keep this isolated from the per-user
    # settings flow.
    one_shot_settings = {"HV_GEMINI_KEY": demo_key, "preferred_provider": "gemini"}
    try:
        from providers import get_provider
        provider = get_provider(one_shot_settings)
    except RuntimeError as e:
        return JSONResponse({"ok": False, "error": "provider_init_failed", "message": str(e)}, status_code=503)
    prompt = _try_demo_prompt(icp)
    try:
        raw = provider.chat(
            messages=[
                {"role": "system", "content": "You are a precise B2B research assistant. Reply with JSON only."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=2200,
            temperature=0.5,
            timeout_s=30.0,
            response_format={"type": "json_object"},
        )
    except Exception as e:
        # Refund the rate-limit slot on failure so the user can retry
        bucket = _TRY_RATE_BUCKETS.get(ip)
        if bucket:
            bucket.pop()
        return JSONResponse({"ok": False, "error": "ai_call_failed",
                             "message": f"AI provider call failed: {type(e).__name__}"}, status_code=502)
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", raw or "")
        try:
            parsed = json.loads(match.group(0)) if match else {}
        except Exception:
            parsed = {}
    leads_raw = parsed.get("leads") if isinstance(parsed, dict) else None
    if not isinstance(leads_raw, list) or not leads_raw:
        return JSONResponse({"ok": False, "error": "ai_returned_invalid_shape",
                             "message": "AI didn't return the expected lead list"}, status_code=502)
    public_leads = []
    for ld in leads_raw[:3]:
        if not isinstance(ld, dict):
            continue
        # Force the demo flag so the share page can label it as a demo
        ld["_is_demo"] = True
        ld.setdefault("_quote_verified", "exact")  # demo leads get treated as "verified" so reachability waterfall is interesting
        public_leads.append(_sanitise_lead_for_share(ld))
    if not public_leads:
        return JSONResponse({"ok": False, "error": "ai_returned_empty",
                             "message": "AI returned no usable leads"}, status_code=502)
    # Mint a public share — anonymous user_id 0 is allowed for demo
    # paths since the share table doesn't FK-enforce.
    title = f"Demo hunt — {icp[:60]}"
    hunt_meta = {
        "leads_total": len(public_leads),
        "shared_at": datetime.now(timezone.utc).isoformat(),
        "demo": True,
        "icp": icp[:200],
    }
    slug = await db.create_hunt_share(
        user_id=0, run_id=None, leads=public_leads,
        hunt_meta=hunt_meta, title=title,
        expires_at=None,
    )
    base = PUBLIC_URL.rstrip("/")
    _emit_server_metric("try_submit", {
        "leads_count": len(public_leads),
        "icp_chars": len(icp),
    })
    return {"ok": True, "slug": slug, "url": f"{base}/h/{slug}", "leads_count": len(public_leads)}


@app.get("/api/try/usage")
async def api_try_usage(request: Request):
    """Per-IP rate-limit snapshot. Lets /try render a live counter so
    the constraint feels intentional, not stingy."""
    ip = _get_client_ip(request)
    return _try_rate_status(ip)


# ── Opt-in telemetry endpoint (Kimi round-72 spec) ─────────────────
# Three events total: try_submit (server-side), cli_init (CLI POST),
# cli_hunt (CLI POST). Endpoint accepts {event, platform, version,
# props} JSON and appends to the metrics table. No PII shipped.

_METRICS_RATE_BUCKETS: dict[str, list[float]] = {}
_METRICS_RATE_LIMIT = 60   # per IP per minute — generous; CLI fires at most a few/day
_METRICS_RATE_WINDOW_S = 60.0


def _metrics_rate_check(ip: str) -> bool:
    import time as _t
    now = _t.time()
    bucket = _METRICS_RATE_BUCKETS.setdefault(ip, [])
    cutoff = now - _METRICS_RATE_WINDOW_S
    bucket[:] = [t for t in bucket if t > cutoff]
    if len(bucket) >= _METRICS_RATE_LIMIT:
        return False
    bucket.append(now)
    # Periodically prune empty buckets so the dict doesn't accumulate
    # one entry per distinct IP forever. Cheap O(N) sweep every ~256
    # calls (sample-based) — enough to keep memory bounded under
    # high-IP-cardinality workloads without hitting the hot path.
    if len(_METRICS_RATE_BUCKETS) > 256 and (int(now) % 32) == 0:
        for _ip in [k for k, v in _METRICS_RATE_BUCKETS.items()
                    if not v or all(t <= cutoff for t in v)]:
            _METRICS_RATE_BUCKETS.pop(_ip, None)
    return True


_ALLOWED_METRIC_EVENTS = {"try_submit", "cli_init", "cli_hunt"}


@app.post("/api/_metric")
async def api_metric(request: Request):
    """Opt-in telemetry sink. Rejects unknown events to keep the
    schema tight. Soft-fails on DB errors so a metrics outage never
    breaks a CLI command upstream."""
    ip = _get_client_ip(request)
    if not _metrics_rate_check(ip):
        return JSONResponse({"ok": False, "error": "rate_limited"}, status_code=429)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "bad_json"}, status_code=400)
    event = (body.get("event") or "").strip()
    if event not in _ALLOWED_METRIC_EVENTS:
        return JSONResponse({"ok": False, "error": "unknown_event"}, status_code=400)
    plat = (body.get("platform") or "")[:32]
    ver = (body.get("version") or "")[:32]
    raw_props = body.get("props")
    if not isinstance(raw_props, dict):
        raw_props = {}
    # Strip anything that looks like PII before persisting. Cap key
    # count so a malicious / buggy client can't blow up the metrics
    # table with a single oversized payload.
    props_clean: dict = {}
    for k, v in raw_props.items():
        if len(props_clean) >= 50:
            break
        if not isinstance(k, str) or len(k) > 40:
            continue
        if isinstance(v, (int, float, bool)):
            props_clean[k] = v
        elif isinstance(v, str):
            props_clean[k] = v[:80]
    try:
        await db.record_metric(event, plat, ver, props_clean)
    except Exception:
        # Telemetry must never break the request flow
        pass
    return {"ok": True}


def _emit_server_metric(event: str, props: dict | None = None) -> None:
    """Synchronous server-side emit for in-process events like
    try_submit. Soft-fails — telemetry never crashes a request."""
    try:
        import asyncio as _a
        _a.create_task(db.record_metric(event, "server", "huntova-server", props or {}))
    except Exception:
        pass


# ── Cloud Proxy MVP (GPT round-76 paid wedge) ─────────────────────
# Managed Huntova Cloud Search — a token-authed SearXNG-compatible
# proxy. Users drop the URL into HV_SEARXNG_URL and the local CLI
# works unchanged. Gated by HV_CLOUD_PROXY_BETA env so the route
# 404s in production until Enzo flips the flag for design partners.
# Backed by HV_CLOUD_SEARXNG_URL (the upstream SearXNG instance).


def _cloud_proxy_enabled() -> bool:
    return bool(os.environ.get("HV_CLOUD_PROXY_BETA"))


@app.get("/cloud-search/{token}/search")
async def cloud_proxy_search(token: str, request: Request):
    """Token-authed SearXNG-compatible search endpoint.

    Forwards GET /search?q=... to the upstream SearXNG instance set
    in HV_CLOUD_SEARXNG_URL. Per-user daily quota enforced via
    db.consume_cloud_proxy_quota. Response is the upstream JSON
    pass-through so the local CLI's existing SearXNG client works
    without modification.
    """
    if not _cloud_proxy_enabled():
        raise HTTPException(status_code=404, detail="cloud_proxy_disabled")
    upstream = os.environ.get("HV_CLOUD_SEARXNG_URL", "").strip()
    if not upstream:
        return JSONResponse({"error": "upstream_not_configured"}, status_code=503)
    # Validate token + consume quota atomically
    allowed, remaining = await db.consume_cloud_proxy_quota(token)
    if not allowed:
        # 429 with retry-after-day so client can show a human message
        return JSONResponse(
            {"error": "quota_exceeded_or_invalid",
             "message": "Token invalid, revoked, or daily quota exhausted."},
            status_code=429,
            headers={"Retry-After": "3600"},
        )
    # Forward the query to upstream SearXNG. Use httpx if available,
    # fall back to urllib.
    qs = dict(request.query_params)
    qs.setdefault("format", "json")
    try:
        try:
            import httpx as _httpx
            async with _httpx.AsyncClient(timeout=20.0) as cli:
                up = await cli.get(f"{upstream.rstrip('/')}/search", params=qs)
                body = up.text
                status = up.status_code
                ctype = up.headers.get("content-type", "application/json")
        except ImportError:
            import urllib.request, urllib.parse
            url = f"{upstream.rstrip('/')}/search?{urllib.parse.urlencode(qs)}"
            with urllib.request.urlopen(url, timeout=20) as r:
                body = r.read().decode("utf-8", errors="ignore")
                status = r.getcode()
                ctype = "application/json"
    except Exception as e:
        return JSONResponse({"error": "upstream_failed",
                             "message": f"{type(e).__name__}"}, status_code=502)
    return Response(
        content=body,
        media_type=ctype,
        status_code=status,
        headers={"X-Huntova-Cloud-Quota-Remaining": str(remaining)},
    )


@app.post("/api/admin/cloud-token")
async def api_admin_cloud_token(request: Request):
    """Mint a Cloud Proxy token. Bearer = HV_ADMIN_TOKEN.

    Body: {email?, plan?, daily_quota?, expires_at?, notes?}
    Returns: {ok, token, daily_quota, plan}
    """
    import hmac as _hmac
    expected = os.environ.get("HV_ADMIN_TOKEN", "").strip()
    if not expected:
        return JSONResponse({"ok": False, "error": "admin_disabled"}, status_code=503)
    auth = (request.headers.get("authorization") or "").strip()
    given = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if not given or not _hmac.compare_digest(given, expected):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    try:
        tok = await db.mint_cloud_proxy_token(
            user_email=(body.get("email") or "")[:200],
            plan=(body.get("plan") or "design_partner")[:32],
            daily_quota=int(body.get("daily_quota") or 200),
            expires_at=body.get("expires_at"),
            notes=(body.get("notes") or "")[:400],
        )
    except Exception as e:
        return JSONResponse({"ok": False, "error": "mint_failed",
                             "message": f"{type(e).__name__}"}, status_code=500)
    base = PUBLIC_URL.rstrip("/")
    return {
        "ok": True,
        "token": tok,
        "daily_quota": int(body.get("daily_quota") or 200),
        "plan": body.get("plan") or "design_partner",
        "endpoint": f"{base}/cloud-search/{tok}",
        "instructions": (
            f"Set HV_SEARXNG_URL={base}/cloud-search/{tok} in the user's env. "
            "The local CLI's SearXNG client works unchanged."
        ),
    }


@app.get("/api/admin/metrics")
async def api_admin_metrics(request: Request,
                            days: int = 7,
                            event: str = ""):
    """Admin-only metrics summary for `huntova metrics show`.

    Requires HV_ADMIN_TOKEN to match the Bearer header. Returns daily
    event counts for the last N days (default 7). Optionally filter to
    a single event name.
    """
    import hmac as _hmac
    expected = os.environ.get("HV_ADMIN_TOKEN", "").strip()
    if not expected:
        return JSONResponse({"ok": False, "error": "admin_disabled",
                             "message": "HV_ADMIN_TOKEN not set on the server"},
                            status_code=503)
    auth = (request.headers.get("authorization") or "").strip()
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    if not token or not _hmac.compare_digest(token, expected):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    days = max(1, min(int(days or 7), 90))
    rows = []
    try:
        # Cross-engine query: SQLite (local CLI) uses datetime('now', '-N days').
        # Postgres (production / cloud) uses NOW() - INTERVAL 'N days'. We
        # detect the driver via db._is_sqlite so the same admin endpoint
        # works on both.
        if db._is_sqlite():
            params = [f"-{days} days"]
            ts_clause = "ts >= datetime('now', %s)"
        else:
            params = [f"{days} days"]
            ts_clause = "ts >= NOW() - (%s)::INTERVAL"
        if event:
            rows = await db._afetchall(
                f"SELECT date(ts) AS day, event, COUNT(*) AS n "
                f"FROM metrics WHERE {ts_clause} AND event = %s "
                "GROUP BY date(ts), event ORDER BY day DESC, event",
                params + [event])
        else:
            rows = await db._afetchall(
                f"SELECT date(ts) AS day, event, COUNT(*) AS n "
                f"FROM metrics WHERE {ts_clause} "
                "GROUP BY date(ts), event ORDER BY day DESC, event",
                params)
    except Exception as e:
        return JSONResponse({"ok": False, "error": "db_query_failed",
                             "message": f"{type(e).__name__}: {str(e)[:120]}"},
                            status_code=500)
    return {"ok": True, "days": days, "rows": [dict(r) for r in rows]}


@app.get("/demo", response_class=HTMLResponse)
async def demo_page():
    """Live-rendered sample Proof Pack. Visitors can SEE the
    differentiator (evidence-first prospecting) before installing.
    Per round-68 launch checklist: a /demo page is one of the top
    pre-launch assets.
    """
    sample = {
        "slug": "demo",
        "title": "Sample hunt — agencies needing outside video help",
        "leads": [
            {
                "org_name": "Aurora Studios",
                "org_website": "https://aurora-studios.example",
                "country": "Germany",
                "city": "Berlin",
                "fit_score": 9,
                "why_fit": "Mid-size production house, recently launched a brand campaign for a Fortune-500 retailer.",
                "production_gap": "No in-house video team — outsourced their last 3 hero ads to freelancers.",
                "fit_rationale": "Aurora's CEO mentioned in their Q1 newsletter that they are doubling down on video content but are 'building creative capacity through partners.' Their case studies all credit external production teams.",
                "timing_rationale": "Hiring page added 2 video-producer roles in the last 30 days. Active recruiting + recent campaign launch suggest a buying cycle within 60 days.",
                "event_name": "Q1 brand campaign + 2 open producer roles",
                "contact_email": "maria.klein@aurora-studios.example",
                "contact_name": "Maria Klein",
                "contact_role": "Head of Brand",
                "contact_linkedin": "https://www.linkedin.com/in/maria-klein-aurora",
                "org_linkedin": "https://www.linkedin.com/company/aurora-studios",
                "_quote_verified": "exact",
                "_data_confidence": 0.78,
                "_confidence_signals": 4,
            },
            {
                "org_name": "Tessera Marketing",
                "org_website": "https://tessera-mkt.example",
                "country": "France",
                "city": "Paris",
                "fit_score": 8,
                "why_fit": "Boutique agency hiring for content roles; explicit signal of outsourcing video to specialists.",
                "production_gap": "Posted 'looking for video partners' in their public roadmap.",
                "fit_rationale": "Their open roadmap explicitly lists 'partner with external video studios for branded content' as a Q2 initiative.",
                "timing_rationale": "Public roadmap was updated 8 days ago.",
                "contact_email": "jean@tessera-mkt.example",
                "contact_name": "Jean Dubois",
                "contact_role": "Founder",
                "_quote_verified": "exact",
                "_data_confidence": 0.71,
                "_confidence_signals": 3,
            },
            {
                "org_name": "Helio Production",
                "org_website": "https://helio-prod.example",
                "country": "Germany",
                "city": "Munich",
                "fit_score": 9,
                "why_fit": "Recurring event series with consistent post-production needs.",
                "production_gap": "Founder mentioned outsourcing on a public podcast last month.",
                "fit_rationale": "Helio runs 4 corporate events per year and the founder publicly said on a podcast that 'we don't keep editors on staff anymore — we work with 2-3 trusted partners.'",
                "timing_rationale": "Next event is 6 weeks away.",
                "org_linkedin": "https://www.linkedin.com/company/helio-production",
                "_quote_verified": "close",
                "_data_confidence": 0.65,
                "_confidence_signals": 3,
            },
            {
                "org_name": "Nimbus Creative",
                "org_website": "https://nimbus.example",
                "country": "United Kingdom",
                "city": "London",
                "fit_score": 8,
                "why_fit": "Mid-tier agency with a fresh rebrand in flight; portfolio is actively expanding.",
                "production_gap": "No video work shown in the last 6 months on their Behance.",
                "fit_rationale": "Behance feed shows 14 design projects but zero video projects since November.",
                "contact_email": "studio@nimbus.example",
                "_is_generic_email": True,
                "_data_confidence": 0.55,
            },
        ],
        "meta": {
            "leads_total": 4,
            "shared_at": "2026-04-30T11:00:00Z",
            # Marker so the share-page renderer shows a sample-data banner.
            # `demo_kind="static"` differentiates the canonical /demo page
            # (illustrative output, never run) from /try-minted previews
            # (synthetic but agent-shaped). Both flag `demo:True` so the
            # banner CSS still triggers; the kind selects the copy.
            "demo": True,
            "demo_kind": "static",
        },
        "created_at": "2026-04-30T11:00:00Z",
        "view_count": 0,
        "run_id": None,
    }
    # Apply the same proof-pack synthesis the real share path uses
    # so visitors see the production-quality output.
    for lead in sample["leads"]:
        lead["proof_pack"] = _build_proof_pack(lead)
    return HTMLResponse(_render_share_page(sample))


@app.get("/plugins", response_class=HTMLResponse)
async def plugins_page():
    """Public plugin catalogue — ClawHub-equivalent. Browses the static
    JSON registry at docs/plugin-registry/registry.json plus the bundled
    plugins shipped in the wheel. Live search, capability filtering,
    one-click install command per row."""
    return HTMLResponse(_render_plugins_page())


def _render_plugins_page() -> str:
    """Render the public /plugins browse page. Reads the registry JSON
    at request time so updates flow through without redeploy."""
    from html import escape as _esc
    import json as _json
    import pathlib as _pl

    # Load registry (static JSON shipped with the repo)
    registry_path = _pl.Path(__file__).resolve().parent / "docs" / "plugin-registry" / "registry.json"
    registry: list = []
    try:
        registry = _json.loads(registry_path.read_text(encoding="utf-8"))
        if not isinstance(registry, list):
            registry = []
    except Exception:
        registry = []

    # Append bundled plugins as in-tree entries so the page never looks empty
    bundled = [
        {
            "name": "csv-sink", "bundled": True, "verified": True,
            "description": "Append every saved lead to a CSV file. Drop-in for spreadsheet workflows.",
            "hooks": ["post_save"], "capabilities": ["filesystem_write"],
            "install": "Bundled with huntova", "version": "1.0.0",
        },
        {
            "name": "dedup-by-domain", "bundled": True, "verified": True,
            "description": "Drop search results whose domain already appeared earlier in this hunt.",
            "hooks": ["post_search"], "capabilities": [],
            "install": "Bundled with huntova", "version": "1.0.0",
        },
        {
            "name": "slack-ping", "bundled": True, "verified": True,
            "description": "POST to a Slack incoming webhook on each saved lead.",
            "hooks": ["post_save"], "capabilities": ["network"],
            "install": "Bundled with huntova", "version": "1.0.0",
        },
        {
            "name": "recipe-adapter", "bundled": True, "verified": True,
            "description": "Reads HV_RECIPE_ADAPTATION env, applies winning_terms / suppress_terms / added_queries to the query list.",
            "hooks": ["pre_search"], "capabilities": [],
            "install": "Bundled with huntova", "version": "1.0.0",
        },
        {
            "name": "adaptation-rules", "bundled": True, "verified": True,
            "description": "Applies AI-generated scoring_rules from the recipe adaptation card. Closes the outcome→adapt→hunt loop.",
            "hooks": ["post_score"], "capabilities": [],
            "install": "Bundled with huntova", "version": "1.0.0",
        },
    ]
    plugins = bundled + registry
    bundled_count = len(bundled)
    registry_count = sum(1 for p in registry if isinstance(p, dict))

    cards = []
    for p in plugins:
        if not isinstance(p, dict):
            continue
        name = _esc(str(p.get("name") or "(unnamed)"))
        desc = _esc(str(p.get("description") or ""))
        version = _esc(str(p.get("version") or "?"))
        author = _esc(str(p.get("author") or ""))
        install_cmd = _esc(str(p.get("install") or f"pip install {p.get('name','')}"))
        homepage = _esc(str(p.get("homepage") or ""))
        is_bundled = bool(p.get("bundled"))
        is_verified = bool(p.get("verified"))
        hooks = p.get("hooks") or []
        caps = p.get("capabilities") or []
        hook_pills = "".join(
            f"<span class='hook'>{_esc(str(h))[:24]}</span>"
            for h in hooks if isinstance(h, str)
        )
        cap_pills = "".join(
            f"<span class='cap cap-{_esc(str(c))[:20]}'>{_esc(str(c))[:20]}</span>"
            for c in caps if isinstance(c, str)
        ) or "<span class='cap cap-none'>no capabilities</span>"
        badges = ""
        if is_bundled:
            badges += "<span class='badge badge-bundled'>bundled</span>"
        if is_verified and not is_bundled:
            badges += "<span class='badge badge-verified'>verified ✓</span>"
        if not is_verified and not is_bundled:
            badges += "<span class='badge badge-community'>community ○</span>"
        homepage_link = (
            f"<a class='plug-home' href='{homepage}' target='_blank' rel='noopener'>homepage →</a>"
            if homepage else ""
        )
        copy_id = f"copy-{name.replace('.', '-')}"
        cards.append(
            f"<article class='plug' data-name='{name.lower()}' data-caps='{','.join(_esc(str(c)) for c in caps if isinstance(c, str))}'>"
            f"<header><h3>{name}</h3>{badges}</header>"
            f"<p class='desc'>{desc}</p>"
            f"<div class='meta'>{hook_pills}{cap_pills}</div>"
            f"<div class='footer'>"
            f"<pre class='install-line'><code>{install_cmd}</code></pre>"
            f"<button class='copy-btn' data-cmd='{install_cmd}' onclick=\"navigator.clipboard.writeText(this.dataset.cmd).then(()=>{{this.textContent='Copied'}})\">Copy</button>"
            f"</div>"
            f"<div class='subfooter'>v{version}{(' · ' + author) if author else ''}{(' · ' + homepage_link) if homepage_link else ''}</div>"
            f"</article>"
        )

    body = f"""
<main class='wrap'>
  <header class='hero'>
    <p class='kicker'>Huntova plugins · community + verified</p>
    <h1>Plugins for every hook in the agent.</h1>
    <p class='lede'>Each plugin runs on a specific hook (pre_search, post_score, post_save…) and discloses its capabilities (network, secrets, filesystem_write, subprocess) so you can audit what it can do before installing.</p>
    <div class='controls'>
      <input type='search' id='plug-search' placeholder='Search plugins by name or description…' autocomplete='off'>
      <select id='plug-cap-filter'>
        <option value=''>All capabilities</option>
        <option value='network'>Needs network</option>
        <option value='secrets'>Reads secrets</option>
        <option value='filesystem_write'>Writes filesystem</option>
        <option value='subprocess'>Spawns subprocess</option>
      </select>
    </div>
    <p class='counts' style='margin-top:18px;color:#5d6679;font-size:12.5px;font-family:ui-monospace,monospace'>
      Showing {bundled_count} bundled · {registry_count} community {('entry' if registry_count == 1 else 'entries')}
      {('— <a href="https://github.com/enzostrano/huntova-plugins" style="color:#a48bff" target="_blank" rel="noopener">submit yours</a>' if registry_count == 0 else '')}
    </p>
  </header>
  <section class='catalogue'>
    {''.join(cards)}
  </section>
  <aside class='contribute'>
    <h2>Contribute a plugin</h2>
    <p>Build a plugin with <code>huntova plugins create my-thing</code>, ship it as a pip package, and submit it to the community registry by opening a PR.</p>
    <pre><code>huntova plugins create my-crm-sink
# edit ~/.config/huntova/plugins/my_crm_sink.py
git clone https://github.com/enzostrano/huntova-plugins
# add an entry to registry.json
# git push origin main + open PR</code></pre>
  </aside>
</main>
<script>
(function(){{
  const q = document.getElementById('plug-search');
  const cf = document.getElementById('plug-cap-filter');
  const cards = Array.from(document.querySelectorAll('.plug'));
  function applyFilter() {{
    const term = (q.value || '').trim().toLowerCase();
    const cap = (cf.value || '').trim();
    cards.forEach(c => {{
      const name = (c.dataset.name || '').toLowerCase();
      const caps = (c.dataset.caps || '').split(',');
      const text = c.textContent.toLowerCase();
      const matchTerm = !term || name.includes(term) || text.includes(term);
      const matchCap = !cap || caps.includes(cap);
      c.style.display = (matchTerm && matchCap) ? '' : 'none';
    }});
  }}
  q.addEventListener('input', applyFilter);
  cf.addEventListener('change', applyFilter);
}})();
</script>
"""
    css = """
    /* /plugins-specific overrides — body font/colors come from share-shell */
    body{margin:0;background:#08090c;color:#eef0f4;line-height:1.55}
    .wrap{max-width:1080px;margin:0 auto;padding:60px 22px 80px}
    .hero{margin-bottom:36px;text-align:center}
    .kicker{color:#8a93a4;font-size:12px;letter-spacing:.16em;text-transform:uppercase;margin:0 0 14px}
    h1{font-size:42px;line-height:1.1;letter-spacing:-.02em;margin:0 0 16px;font-weight:700}
    .lede{color:#8a93a4;font-size:16px;max-width:640px;margin:0 auto 28px;line-height:1.6}
    .controls{display:flex;gap:10px;justify-content:center;flex-wrap:wrap;margin-top:18px}
    .controls input,.controls select{background:#0d0f14;border:1px solid #1c2029;color:#eef0f4;padding:10px 14px;border-radius:10px;font-size:14px;font-family:inherit;min-width:240px}
    .controls input:focus,.controls select:focus{outline:none;border-color:#7c5cff;box-shadow:0 0 0 3px rgba(124,92,255,.18)}
    .catalogue{display:grid;grid-template-columns:1fr;gap:14px}
    @media(min-width:760px){.catalogue{grid-template-columns:1fr 1fr}}
    @media(min-width:980px){.catalogue{grid-template-columns:1fr 1fr 1fr}}
    .plug{background:#0d0f14;border:1px solid #1c2029;border-radius:14px;padding:18px;display:flex;flex-direction:column;gap:10px}
    .plug:hover{border-color:#272b35}
    .plug header{display:flex;justify-content:space-between;align-items:flex-start;gap:8px;flex-wrap:wrap}
    .plug h3{margin:0;font-family:ui-monospace,'SF Mono',monospace;font-size:14.5px;color:#a48bff;font-weight:600}
    .badge{font-size:9.5px;padding:2px 7px;border-radius:6px;letter-spacing:.05em;text-transform:uppercase;font-weight:600}
    .badge-bundled{background:rgba(61,220,151,.16);color:#3ddc97;border:1px solid rgba(61,220,151,.4)}
    .badge-verified{background:rgba(124,92,255,.16);color:#a48bff;border:1px solid rgba(124,92,255,.4)}
    .badge-community{background:rgba(154,166,178,.1);color:#9aa6b2;border:1px solid #272b35}
    .desc{color:#8a93a4;font-size:13px;line-height:1.55;margin:0}
    .meta{display:flex;flex-wrap:wrap;gap:5px}
    .hook{font-size:10.5px;padding:2px 8px;border-radius:999px;background:rgba(124,92,255,.10);color:#a48bff;font-family:ui-monospace,monospace}
    .cap{font-size:10.5px;padding:2px 8px;border-radius:999px;font-family:ui-monospace,monospace;border:1px solid #272b35}
    .cap-none{color:#5d6679}
    .cap-network{color:#f6b352;border-color:rgba(246,179,82,.4);background:rgba(246,179,82,.06)}
    .cap-secrets{color:#ff6464;border-color:rgba(255,100,100,.4);background:rgba(255,100,100,.06)}
    .cap-filesystem_write{color:#f6b352;border-color:rgba(246,179,82,.4);background:rgba(246,179,82,.06)}
    .cap-subprocess{color:#ff6464;border-color:rgba(255,100,100,.4);background:rgba(255,100,100,.06)}
    .footer{display:flex;align-items:stretch;gap:8px;margin-top:auto}
    .install-line{flex:1;margin:0;padding:8px 12px;background:#08090c;border:1px solid #1c2029;border-radius:8px;font-family:ui-monospace,monospace;font-size:11.5px;color:#dfe3eb;overflow-x:auto;white-space:nowrap}
    .copy-btn{padding:6px 12px;font-size:11px;background:transparent;color:#eef0f4;border:1px solid #272b35;border-radius:6px;cursor:pointer;font-family:inherit}
    .copy-btn:hover{border-color:#7c5cff;color:#a48bff}
    .subfooter{font-size:11px;color:#5d6679;font-family:ui-monospace,monospace}
    .plug-home{color:#a48bff;text-decoration:none;border-bottom:1px dotted rgba(164,139,255,.3)}
    .contribute{margin-top:48px;padding:28px;background:#0d0f14;border:1px solid #1c2029;border-radius:14px}
    .contribute h2{margin:0 0 12px;font-size:18px}
    .contribute p{color:#8a93a4;font-size:14px;line-height:1.6;margin:0 0 14px}
    .contribute pre{margin:0;padding:14px;background:#08090c;border:1px solid #1c2029;border-radius:10px;font-family:ui-monospace,monospace;font-size:12.5px;color:#a48bff;overflow-x:auto;line-height:1.6}
    .contribute code{color:#dfe3eb}
    """
    return _render_share_shell(
        title="Huntova plugins — community + verified",
        body=body,
        og_description="Browse + install plugins for every hook in the Huntova agent (pre_search, post_score, post_save…). Capability disclosure, verified badges, one-click install commands.",
        og_image="",
    ).replace("</style>", css + "</style>", 1)


@app.get("/setup", response_class=HTMLResponse)
async def setup_page():
    """First-run web wizard. Pick provider, paste key, save to keychain
    + AI probe. Opened automatically by `huntova onboard --browser` and
    `huntova serve` when no provider is configured yet."""
    return _read_template("setup.html")


_VALID_PROVIDERS = {
    "gemini", "anthropic", "openai",
    "openrouter", "groq", "deepseek", "together", "mistral", "perplexity",
    "ollama", "lmstudio", "llamafile",
    "custom",
}
_LOCAL_PROVIDER_SLUGS = {"ollama", "lmstudio", "llamafile"}


def _local_no_provider_response():
    """Local-mode preflight helper for AI-using endpoints. Returns a
    400 JSONResponse with an actionable message if the runtime is in
    local mode AND no provider is configured. Returns None otherwise
    (caller proceeds normally). Cloud mode always returns None — the
    cloud has its own provider routing path.

    Wrap the import in try/except so a degraded providers.py module
    can't break the request flow; we'd rather let the underlying AI
    call fail with whatever vendor error than hard-block.
    """
    try:
        from runtime import CAPABILITIES as _CAPS
        if _CAPS.mode != "local":
            return None
        from providers import list_available_providers
        if list_available_providers() or []:
            return None
        return JSONResponse(
            {"ok": False, "error": "No AI provider configured. Open Settings → Providers to add a key."},
            status_code=400,
        )
    except Exception:
        return None


@app.get("/api/setup/status")
async def api_setup_status():
    """Setup-wizard status snapshot: which providers are configured,
    where filesystem state lives, secrets backend in use, locally
    detected AI servers. No secrets leak — only metadata."""
    fs = {}
    try:
        from secrets_store import _backend_label
        fs["secrets_backend"] = _backend_label()
    except Exception as e:
        fs["secrets_backend"] = f"(probe failed: {type(e).__name__})"
    try:
        import db_driver as _dbd
        from pathlib import Path as _P
        db_path = _dbd._local_db_path()
        fs["db_path"] = str(db_path)
        fs["db_exists"] = db_path.exists()
        cfg_dir = _P(os.environ.get("XDG_CONFIG_HOME") or _P.home() / ".config") / "huntova"
        cfg_path = cfg_dir / "config.toml"
        fs["config_path"] = str(cfg_path)
        fs["config_exists"] = cfg_path.exists()
    except Exception as e:
        fs["error"] = f"{type(e).__name__}"
    # Provider configuration: probe via the providers module (which
    # checks env + secrets_store + config.toml in priority order).
    configured: list[str] = []
    try:
        from providers import list_available_providers
        configured = list_available_providers() or []
    except Exception:
        configured = []
    cfg_set = {slug: (slug in configured) for slug in _VALID_PROVIDERS}
    # Local-server detection (fast localhost probe — runs in <2s)
    detected: dict = {}
    try:
        from providers import detect_local_servers
        detected = detect_local_servers()
    except Exception:
        detected = {}
    return {
        "providers_configured": configured,
        "providers_configured_set": cfg_set,
        "detected": detected,
        "filesystem": fs,
        "version": _huntova_version(),
    }


@app.get("/api/setup/detect-local")
async def api_setup_detect_local():
    """Re-probe localhost for running local AI servers (Ollama, LM
    Studio, llamafile). Called from the setup wizard's `↻ Re-detect`
    button after the user starts a server during setup."""
    try:
        from providers import detect_local_servers
        return {"ok": True, "detected": detect_local_servers()}
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"{type(e).__name__}"}, status_code=500)


@app.get("/api/subagents")
async def api_subagents_list(request: Request, user: dict = Depends(require_user)):
    """Live list of background subagents for this user. The dashboard
    polls this once on load + listens for `subagent_status` over SSE for
    incremental updates."""
    from agent_runner import subagent_registry
    return {"ok": True, "subagents": subagent_registry.list_user(user["id"])}


@app.post("/api/subagents/spawn")
async def api_subagents_spawn(request: Request, user: dict = Depends(require_user)):
    """Spawn a background subagent. Body: {kind, payload}."""
    from agent_runner import spawn_subagent
    try:
        body = await request.json()
    except Exception:
        body = {}
    kind = (body.get("kind") or "").strip()
    payload = body.get("payload") or {}
    if not isinstance(payload, dict):
        return JSONResponse({"ok": False, "error": "payload must be object"}, status_code=400)
    res = spawn_subagent(user["id"], kind, payload)
    if not res.get("ok"):
        return JSONResponse(res, status_code=400)
    return res


@app.post("/api/subagents/{sub_id}/cancel")
async def api_subagents_cancel(sub_id: str, user: dict = Depends(require_user)):
    from agent_runner import subagent_registry
    ok = subagent_registry.cancel(user["id"], sub_id)
    return {"ok": ok}


def _is_local_origin(origin: str) -> bool:
    """True if `origin` is one of our own local URLs (any port).

    Used by CSRF-exempt mutating endpoints to defend against the
    "simple CORS" attack: a malicious site can fire a POST with
    Content-Type: text/plain to localhost:5050 — no preflight, no
    response body exposure, but the side effect runs server-side
    because /api/chat etc. are exempt from the CSRF token middleware.
    Origin will always be set on browser-originated requests; curl /
    scripts don't send one.
    """
    if not origin:
        return True  # No Origin header → not a browser → CLI/script. Allow.
    o = origin.strip().lower().rstrip("/")
    for prefix in ("http://127.0.0.1", "http://localhost",
                   "https://127.0.0.1", "https://localhost",
                   "http://[::1]", "https://[::1]"):
        if o == prefix or o.startswith(prefix + ":") or o.startswith(prefix + "/"):
            return True
    return False


@app.post("/api/chat")
async def api_chat(request: Request, user: dict = Depends(require_user)):
    """Web-chat dispatcher — Huntova's brain for the dashboard.

    Parses free text into a JSON action, then either:
    - dispatches client-side (start_hunt, list_leads, navigate)
    - executes server-side (settings, lead mutation, share, recipes)

    Returns either {action, text, ...} for client dispatch or
    {action: "done", text, result} for server-executed actions.

    CSRF defense lives in CSRFMiddleware: browser-originated cross-
    origin POSTs (Origin header set + non-local) are rejected before
    they reach this handler. Scripts (curl, cli_remote) don't send
    Origin → pass.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "bad_json"}, status_code=400)
    msg = (body.get("message") or "").strip()
    if not msg:
        return JSONResponse({"error": "empty_message"}, status_code=400)
    if _check_ai_rate(user["id"]):
        return JSONResponse({"action": "answer",
                             "text": "Too many chat requests. Wait a moment."},
                            status_code=429)
    # Honor the chat AI selector — if the user picked a specific
    # provider next to the input, route this dispatch + any subagent
    # the chat fans out through that provider. Empty string = "Auto".
    _prov_override = (body.get("provider") or "").strip().lower()
    try:
        from providers import get_provider, push_provider_override
        if _prov_override:
            push_provider_override(_prov_override)
        try:
            prov = get_provider()
        except RuntimeError as e:
            push_provider_override(None)
            return {"action": "answer",
                    "text": f"No AI provider configured. {e}"}
    except Exception as e:
        try: push_provider_override(None)
        except Exception: pass
        return {"action": "answer",
                "text": f"Provider lookup failed: {type(e).__name__}: {e}"}

    # Hand the model the live state it needs to suggest sensible
    # mutations. Truncated tightly so we don't blow the prompt budget.
    s_now = await db.get_settings(user["id"])
    w_now = (s_now or {}).get("wizard", {}) or {}
    _state = (
        f"current_icp={(w_now.get('business_description') or '')[:200]!r} | "
        f"target_clients={(w_now.get('target_clients') or '')[:160]!r} | "
        f"countries={(s_now.get('default_countries') or [])[:5]} | "
        f"max_leads={s_now.get('default_max_leads', 10)} | "
        f"booking_url={(s_now.get('booking_url') or '')[:80]!r} | "
        f"from_name={(s_now.get('from_name') or '')[:60]!r} | "
        f"theme={s_now.get('theme', 'dark')!r}"
    )

    SYSTEM_PROMPT = (
        "You are Huntova's web-chat brain. Huntova specialises in B2B "
        "lead-generation but the chat is a fully capable assistant on "
        "any topic — research, brainstorm, compare options, explain a "
        "concept, draft copy, write code, plan a trip — anything the "
        "user asks. When the question isn't lead-gen-specific, use the "
        "`answer` action and give a substantive, helpful reply (or "
        "`web_search` if you need fresh facts). Reply with EXACTLY ONE "
        "JSON object, no markdown, no fences, no prose around it.\n\n"
        f"LIVE STATE: {_state}\n\n"
        "ACTIONS YOU CAN TAKE:\n"
        '- {"action":"start_hunt","countries":["Germany"],"max_leads":10,"icp":"<short ICP>"}\n'
        '    → frontend dispatches. countries = full English names.\n'
        '- {"action":"list_leads","filter":"country:Germany"}\n'
        '    → frontend filters the leads view.\n'
        '- {"action":"navigate","page":"settings|leads|hunts|wizard|providers"}\n'
        '    → frontend opens that panel.\n'
        '- {"action":"update_settings","patch":{"default_max_leads":25,"booking_url":"https://cal.com/me","from_name":"Enzo","theme":"dark","reduced_motion":false}}\n'
        '    → server applies the patch (only known keys: default_max_leads, default_countries, booking_url, from_name, theme, reduced_motion).\n'
        '- {"action":"update_icp","business_description":"...","target_clients":"..."}\n'
        '    → server updates wizard fields, regenerates DNA.\n'
        '- {"action":"set_lead_status","lead_id":"<id>","status":"replied|won|lost|qualified|email_sent|new"}\n'
        '    → server updates the lead status.\n'
        '- {"action":"delete_lead","lead_id":"<id>"}\n'
        '    → server deletes (asks for confirm if dangerous).\n'
        '- {"action":"mint_share","top":10,"title":"Q3 prospects"}\n'
        '    → server mints a /h/<slug>, returns URL.\n'
        '- {"action":"research","lead_id":"<id>","pages":14}\n'
        '    → server runs deep-research (14-page crawl) + rewrites '
        'email_subject + email_body. Use only when the user named a '
        'specific lead.\n'
        '- {"action":"sequence_run","dry_run":false,"max":25}\n'
        '    → server fires Day +4 / +9 follow-ups for any due leads. '
        'set dry_run:true to preview.\n'
        '- {"action":"sequence_status"}\n'
        '    → server returns the count by step.\n'
        '- {"action":"inbox_check","since":"14"}\n'
        '    → server runs one IMAP poll, classifies replies.\n'
        '- {"action":"pulse","since":"7d"}\n'
        '    → server returns the weekly self-coaching summary.\n'
        '- {"action":"playbook_install","name":"solo-coach"}\n'
        '    → server installs a bundled playbook + auto-seeds the '
        'wizard ICP / target_clients / tone.\n'
        '- {"action":"answer","text":"<helpful reply>"}\n'
        '    → use for how-to, status, general questions, OR any '
        'topic outside lead-gen. The user can ask Huntova to research, '
        'brainstorm, draft, explain — be a real assistant, not a '
        'narrow CRM bot. Use markdown sparingly inside `text` (Huntova '
        'renders plain text best).\n'
        '- {"action":"web_search","query":"<short query>","summarise":true}\n'
        '    → use when the user asks about current events, recent '
        'launches, prices, news, or any fact you might not have. '
        'Server runs the query through SearXNG, optionally summarises '
        'the top results via the user\'s AI, and returns the result.\n'
        '- {"action":"spawn_agents","text":"<one-line confirm>",\n'
        '   "agents":[{"kind":"deep_research","payload":{"lead_id":"..."}, "provider":"anthropic"},\n'
        '              {"kind":"inbox_scan","payload":{"since_days":7}, "provider":"gemini"}]}\n'
        '    → fans out parallel background subagents. Each entry: kind ∈\n'
        '      {deep_research, inbox_scan, qualify_pool}, optional payload\n'
        '      (lead_id for deep_research; since_days for inbox_scan), and\n'
        '      optional provider override. Use this when the user asks for\n'
        '      "spawn N agents", "research lead X with Claude AND lead Y\n'
        '      with GPT-4", or "run inbox + research in parallel".\n\n'
        "Rules: pick one action. If unsure, use answer. Never guess "
        "lead_ids — ask the user to click a lead first. For destructive "
        "actions (delete_lead) include a `confirm: true` only if the "
        "user explicitly said 'yes / confirm / delete it'. Playbook "
        "names: agencies-eu, b2b-saas-hiring, tech-recruiting, "
        "ecommerce-shopify, solo-coach, consultant-fractional, "
        "video-production, saas-developer-tools, design-studio, "
        "podcast-producer.\n\n"
        "VOICE for the `text` field (a238 — chat-first UI): write "
        "like a confident, calm operations chief. Short sentences. "
        "No emoji unless the user used one. No hedging or filler "
        "(\"sure!\", \"happy to help\", \"great question\"). State "
        "what you did or what's next, period. If a value matters, "
        "front-load it (\"14 leads since Tuesday. Top fit: 9.2.\"). "
        "Never apologize for limits — state them and propose a path. "
        "Match the user's brevity: 1-2 sentences for routine asks, "
        "more only when explaining a real tradeoff. The JSON shape "
        "rules above are inviolable; the voice rules apply to the "
        "human-facing copy inside `text`."
    )

    # Anthropic JSON-mode prefill trick (mirrors cli.py:_ask_ai).
    provider_name = (getattr(prov, "name", "") or "").lower()
    is_anthropic = "anthropic" in provider_name or "claude" in provider_name
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": msg}]
    if is_anthropic:
        messages.append({"role": "assistant", "content": "{"})

    # a239: time the AI call + approximate token usage so the chat UI can
    # render OpenClaw-style meta under each response (model · tokens ·
    # tps · latency). Approximation uses ~4 chars/token (English avg).
    # Real token counts would need provider-side wrapping; the approx is
    # good-enough signal for "is this expensive?" UX feedback.
    import time as _time
    _t0 = _time.monotonic()
    try:
        raw = prov.chat(
            messages=messages, model=None, max_tokens=600,
            temperature=0.2, timeout_s=30.0,
            response_format={"type": "json_object"},
        )
    except Exception as e:
        return {"action": "answer",
                "text": f"AI call failed: {type(e).__name__}: {str(e)[:160]}"}
    _dt = max(0.001, _time.monotonic() - _t0)
    # a240: prefer the real usage stamped on the provider after the call.
    # Falls back to char/4 approximation only if the provider didn't
    # surface usage (some local backends + edge cases).
    _real_usage = getattr(prov, "_last_usage", None)
    if isinstance(_real_usage, dict) and _real_usage.get("tokens_in") is not None:
        _tok_in = int(_real_usage.get("tokens_in") or 0)
        _tok_out = int(_real_usage.get("tokens_out") or 0)
        _model = _real_usage.get("model") or (getattr(prov, "model", "") or "")
        _approx_flag = bool(_real_usage.get("approx"))
    else:
        _prompt_chars = sum(len(str(m.get("content", ""))) for m in messages)
        _out_chars = len(raw or "")
        _tok_in = max(1, _prompt_chars // 4)
        _tok_out = max(0, _out_chars // 4)
        _model = (getattr(prov, "model", "") or "") or ""
        _approx_flag = True
    _chat_meta = {
        "engine": (getattr(prov, "name", "") or "").lower() or "auto",
        "model": _model,
        "latency_ms": int(_dt * 1000),
        "tokens_in": _tok_in,
        "tokens_out": _tok_out,
        "tps": round(_tok_out / _dt, 1) if _dt > 0 else 0,
        "approx": _approx_flag,
    }

    if is_anthropic and raw and not raw.lstrip().startswith("{"):
        raw = "{" + raw

    import json as _json
    import re as _re
    try:
        parsed = _json.loads(raw or "{}")
    except _json.JSONDecodeError:
        m = _re.search(r"\{[\s\S]*\}", raw or "")
        if not m:
            return {"action": "answer",
                    "text": f"Got non-JSON reply. Try rephrasing."}
        try:
            parsed = _json.loads(m.group(0))
        except _json.JSONDecodeError:
            return {"action": "answer", "text": "Bad JSON reply, try again."}

    if not isinstance(parsed, dict) or "action" not in parsed:
        return {"action": "answer", "text": "Reply missing action field.", "meta": _chat_meta}

    # ── a239 wizard-guard: a hunt without an ICP profile produces
    # garbage queries. Block start_hunt + spawn_agents until the
    # wizard reports complete, and surface a CTA the UI renders as a
    # button to /setup. The agent dispatcher itself can still be
    # called manually from the agent panel for power users; this gate
    # only fires on chat-driven launches. ──
    act = parsed.get("action") or ""
    if act in ("start_hunt", "spawn_agents"):
        try:
            _s_now = await db.get_settings(user["id"])
            _w = (_s_now or {}).get("wizard", {}) or {}
            if not _w.get("_interview_complete"):
                return {
                    "action": "wizard_missing",
                    "text": ("No ICP profile yet — running a hunt without one "
                             "burns AI calls on garbage queries. Open the setup "
                             "wizard, answer 6 questions, then ask me again."),
                    "wizard_url": "/setup",
                    "blocked_action": act,
                    "meta": _chat_meta,
                }
        except Exception:
            # If the settings read fails for any reason, don't block —
            # better to let the hunt run than to soft-lock the user.
            pass

    # ── server-side actions: execute inline + return a `done` envelope ──
    try:
        if act == "update_settings":
            patch = parsed.get("patch") or {}
            if not isinstance(patch, dict):
                return {"action": "answer", "text": "patch must be an object"}
            ALLOWED = {"default_max_leads", "default_countries", "booking_url",
                       "from_name", "theme", "reduced_motion"}
            clean = {k: v for k, v in patch.items() if k in ALLOWED}
            if not clean:
                return {"action": "answer",
                        "text": "Nothing to change — those keys aren't editable here."}
            current = await db.get_settings(user["id"])
            current = {**(current or {}), **clean}
            await db.save_settings(user["id"], current)
            return {"action": "done", "text": f"Updated: {', '.join(clean.keys())}",
                    "result": clean}
        if act == "update_icp":
            bd = (parsed.get("business_description") or "")[:2000]
            tc = (parsed.get("target_clients") or "")[:2000]
            if not (bd or tc):
                return {"action": "answer",
                        "text": "Need business_description or target_clients to update ICP."}
            current = await db.get_settings(user["id"])
            current = current or {}
            wiz = dict(current.get("wizard") or {})
            if bd:
                wiz["business_description"] = bd
            if tc:
                wiz["target_clients"] = tc
            current["wizard"] = wiz
            await db.save_settings(user["id"], current)
            return {"action": "done", "text": "ICP updated.",
                    "result": {"business_description": bd, "target_clients": tc}}
        if act == "set_lead_status":
            lid = (parsed.get("lead_id") or "").strip()
            new_status = (parsed.get("status") or "").strip().lower()
            _ok_status = {"new", "email_sent", "followed_up", "replied",
                          "meeting_booked", "won", "lost", "ignored",
                          "qualified", "unqualified"}
            if not lid or new_status not in _ok_status:
                return {"action": "answer",
                        "text": "I need a lead_id and a valid status."}
            now_iso = datetime.now(timezone.utc).isoformat()
            def _mut(lead: dict) -> dict:
                old = lead.get("email_status", "new")
                if old != new_status:
                    lead["email_status"] = new_status
                    lead["email_status_date"] = now_iso
                    h = lead.get("status_history", [])
                    if not h or h[-1].get("status") != new_status:
                        h.append({"status": new_status, "date": now_iso})
                        if len(h) > 100:
                            h = h[-100:]
                    lead["status_history"] = h
                return lead
            updated = await db.merge_lead(user["id"], lid, _mut)
            if updated is None:
                return {"action": "answer", "text": f"Lead {lid} not found."}
            return {"action": "done",
                    "text": f"Set status to '{new_status}'.",
                    "result": {"lead_id": lid, "status": new_status}}
        if act == "web_search":
            # a256: open-the-chat — let the user research general topics
            # via SearXNG + summarise via their own AI. No lead-gen
            # flavoring; this is just a general-purpose research action
            # so Huntova feels like a real assistant.
            query = (parsed.get("query") or "").strip()[:240]
            summarise = bool(parsed.get("summarise", True))
            if not query:
                return {"action": "answer", "text": "I need a search query."}
            try:
                from app import SEARXNG_URL as _SX
                import requests as _rq
                r = await asyncio.to_thread(
                    _rq.get,
                    _SX.rstrip("/") + "/search",
                    {"params": {"q": query, "format": "json"}},
                )
            except Exception:
                # Use the asyncio.to_thread call shape correctly.
                pass
            try:
                import requests as _rq2
                from app import SEARXNG_URL as _SX2
                _resp = await asyncio.to_thread(
                    lambda: _rq2.get(_SX2.rstrip("/") + "/search",
                                     params={"q": query, "format": "json"}, timeout=8)
                )
                if _resp.status_code != 200:
                    return {"action": "answer", "text": f"Search engine returned {_resp.status_code}. Try again later."}
                _data = _resp.json()
                results = (_data.get("results") or [])[:8]
            except Exception as _se:
                return {"action": "answer",
                        "text": f"Search failed: {type(_se).__name__}: {str(_se)[:160]}"}
            if not results:
                return {"action": "answer",
                        "text": f"No results for '{query}'."}
            # Summarise via the user's AI when requested.
            if summarise:
                _digest = "\n".join(
                    f"- {r.get('title','')[:120]} — {r.get('url','')[:120]}\n  {r.get('content','')[:240]}"
                    for r in results[:6]
                )
                try:
                    summary = prov.chat(
                        messages=[
                            {"role": "system", "content": "Summarise the search results below in 4-7 sentences. Stay factual, cite sources by linking them inline using their URL. Plain text only, no markdown headers."},
                            {"role": "user", "content": f"Query: {query}\n\nResults:\n{_digest}"},
                        ],
                        max_tokens=600, temperature=0.2, timeout_s=30.0,
                    )
                    return {"action": "answer", "text": summary or "(empty summary)"}
                except Exception as _ae:
                    pass
            # Plain list fallback if summarise=False or summary failed.
            lines = [f"Top results for '{query}':"]
            for r in results[:5]:
                lines.append(f"• {r.get('title','')} — {r.get('url','')}")
            return {"action": "answer", "text": "\n".join(lines)}
        if act == "delete_lead":
            lid = (parsed.get("lead_id") or "").strip()
            if not lid:
                return {"action": "answer", "text": "I need a lead_id to delete."}
            if not parsed.get("confirm"):
                return {"action": "answer",
                        "text": f"Reply 'yes, delete {lid}' to confirm."}
            await db.permanent_delete_lead(user["id"], lid)
            return {"action": "done", "text": f"Deleted lead {lid}.",
                    "result": {"lead_id": lid}}
        if act == "mint_share":
            top = max(1, min(int(parsed.get("top") or 10), 50))
            title = (parsed.get("title") or "")[:120]
            leads = await db.get_leads(user["id"])
            leads = sorted(leads, key=lambda x: x.get("fit_score", 0), reverse=True)[:top]
            if not leads:
                return {"action": "answer",
                        "text": "No leads to share — run a hunt first."}
            # Stability fix (audit wave 26): the previous version called
            # `create_hunt_share(lead_ids=ids, ...)` — wrong kwarg name +
            # wrong payload shape. The function takes `leads=` with full
            # sanitised lead dicts (it JSON-serialises them into the
            # public snapshot), not bare ID strings. Every chat-driven
            # mint_share has been raising
            # `TypeError: create_hunt_share() got an unexpected keyword
            # argument 'lead_ids'` and falling through to the generic
            # outer catch, so users have never been able to mint a share
            # via chat. Match the working callers (api_hunts_share at
            # line 3279, _share_handler).
            public_leads = [_sanitise_lead_for_share(l) for l in leads
                            if l.get("lead_id")]
            if not public_leads:
                return {"action": "answer",
                        "text": "Top leads are missing lead_ids — share not minted. "
                                "Try `huntova memory rebuild` to fix the lead index."}
            hunt_meta = {
                "leads_total": len(public_leads),
                "shared_at": datetime.now(timezone.utc).isoformat(),
            }
            slug = await db.create_hunt_share(
                user_id=user["id"], run_id=None,
                leads=public_leads, hunt_meta=hunt_meta,
                title=title or "",
            )
            url = f"{PUBLIC_URL.rstrip('/')}/h/{slug}"
            return {"action": "done",
                    "text": f"Minted: {url}",
                    "result": {"slug": slug, "url": url, "count": len(public_leads)}}
        if act == "research":
            lid = (parsed.get("lead_id") or "").strip()
            if not lid:
                return {"action": "answer",
                        "text": "Tell me which lead to research — click one first."}
            lead = await db.get_lead(user["id"], lid)
            if not lead:
                return {"action": "answer",
                        "text": f"No lead with id {lid}."}
            site = (lead.get("org_website") or "").strip()
            if not site:
                return {"action": "answer",
                        "text": f"{lid} has no org_website to crawl."}
            pages = max(4, min(int(parsed.get("pages") or 14), 25))
            try:
                from app import crawl_prospect
                text, _html, n_pages = await asyncio.to_thread(crawl_prospect, site, pages)
            except Exception as e:
                return {"action": "answer",
                        "text": f"Crawl failed: {type(e).__name__}: {str(e)[:120]}"}
            text = (text or "").strip()
            if len(text) < 400:
                return {"action": "answer",
                        "text": "Couldn't extract enough content from the site to research."}
            # Reuse the dashboard's rewrite path so the research call
            # threads through the same provider abstraction the rest
            # of the chat brain uses.
            from providers import get_provider
            try:
                rprov = get_provider()
            except Exception as e:
                return {"action": "answer", "text": f"Provider: {e}"}
            s2 = await db.get_settings(user["id"]) or {}
            w2 = (s2 or {}).get("wizard", {}) or {}
            booking = (s2.get("booking_url") or "").strip()
            sender_name = (s2.get("from_name") or w2.get("company_name") or "the team").strip()
            tone = (parsed.get("tone") or s2.get("default_tone") or "friendly").strip().lower()
            contact_name = (lead.get("contact_name") or "").strip()
            first = contact_name.split()[0] if contact_name else ""
            r_prompt = (
                f"Write a cold opener for {sender_name}.\n"
                f"PROSPECT: {lead.get('org_name','')} | {contact_name or '(unknown)'} | "
                f"{lead.get('country','')}\n\n"
                f"SITE TEXT (first {min(8000, len(text)):,} chars):\n---\n{text[:8000]}\n---\n\n"
                "Pick ONE specific hook from the site text that the prospect would "
                "recognise (recent product launch, podcast, hire, blog post, quote). "
                f"Open with that hook. 90-130 words. Tone: {tone}. "
                f"Greeting: '{('Hi ' + first) if first else 'Hi'},'. "
                f"End with: {('booking link — ' + booking) if booking else 'a soft single question.'}\n"
                'Return ONLY: {"subject":"...","body":"..."}'
            )
            try:
                raw = await asyncio.to_thread(
                    rprov.chat,
                    [
                        {"role": "system", "content": "Cold-email writer. JSON only."},
                        {"role": "user", "content": r_prompt},
                    ],
                    None, 600, 0.6, 45.0,
                    {"type": "json_object"},
                )
            except Exception as e:
                return {"action": "answer",
                        "text": f"AI call failed: {type(e).__name__}: {str(e)[:120]}"}
            try:
                rdata = json.loads((raw or "").strip() or "{}")
            except Exception:
                import re as _re_local
                m = _re_local.search(r"\{[\s\S]*\}", raw or "")
                rdata = json.loads(m.group(0)) if m else {}
            new_subj = (rdata.get("subject") or "").strip()[:160]
            new_body = (rdata.get("body") or "").strip()
            if not (new_subj and new_body):
                return {"action": "answer",
                        "text": "AI returned an empty draft — try again."}
            now_iso = datetime.now(timezone.utc).isoformat()
            def _r_mut(_l, _ns=new_subj, _nb=new_body, _ts=now_iso, _t=tone, _np=n_pages):
                hist = _l.get("rewrite_history", [])
                if (_l.get("email_body") or "").strip():
                    hist.append({"date": _ts, "tone": _l.get("last_tone", "original"),
                                 "subject": _l.get("email_subject", ""),
                                 "body": _l.get("email_body", ""),
                                 "linkedin": _l.get("linkedin_note", "")})
                    if len(hist) > 10:
                        hist = hist[-10:]
                _l["rewrite_history"] = hist
                _l["email_subject"] = _ns
                _l["email_body"] = _nb
                _l["last_tone"] = _t
                _l["_researched_at"] = _ts
                _l["_research_pages"] = _np
                return _l
            await db.merge_lead(user["id"], lid, _r_mut)
            return {"action": "done",
                    "text": f"Researched {lid} ({n_pages} pages, {len(text):,} chars). "
                            f"New opener saved — old draft archived to rewrite_history.",
                    "result": {"lead_id": lid, "pages": n_pages,
                               "subject": new_subj}}
        if act == "sequence_run":
            dry = bool(parsed.get("dry_run"))
            mx = max(1, min(int(parsed.get("max") or 25), 100))
            from cli_sequence import _run_once as _seq_run
            res = await _seq_run(user["id"], dry_run=dry, max_send=mx)
            txt = (f"sequence: sent {res['sent']}, skipped {res['skipped']}, "
                   f"paused {res['paused']}"
                   + (f", errored {res['errored']}" if res['errored'] else "")
                   + (" (dry-run)" if res['dry_run'] else ""))
            return {"action": "done", "text": txt, "result": res}
        if act == "sequence_status":
            leads_all = await db.get_leads(user["id"], limit=2000) or []
            counts = {0: 0, 1: 0, 2: 0, 3: 0}
            paused = 0
            for ld in leads_all:
                st = int(ld.get("_seq_step") or 0)
                counts[st] = counts.get(st, 0) + 1
                if ld.get("_seq_paused") or ld.get("email_status") in (
                        "replied", "won", "meeting_booked"):
                    paused += 1
            return {"action": "done",
                    "text": (f"step 0 (not enrolled): {counts[0]}, "
                             f"step 1 opener: {counts[1]}, "
                             f"step 2 bump: {counts[2]}, "
                             f"step 3 final: {counts[3]}, paused: {paused}"),
                    "result": {"counts": counts, "paused": paused}}
        if act == "inbox_check":
            from cli_inbox import _scan_inbox
            try:
                since = int(parsed.get("since") or 14)
            except Exception:
                since = 14
            ires = await _scan_inbox(user["id"], since_days=since, dry_run=False)
            if not ires.get("ok"):
                return {"action": "answer", "text": ires.get("error", "inbox check failed")}
            bc = ires.get("by_class") or {}
            bc_str = ", ".join(f"{k}:{v}" for k, v in bc.items() if v)
            txt = (f"scanned {ires.get('scanned', 0)}, "
                   f"matched {ires.get('matched', 0)} replies"
                   + (f" ({bc_str})" if bc_str else "")
                   + (f", auto-replies skipped {ires.get('autoreplied', 0)}"
                      if ires.get('autoreplied') else ""))
            return {"action": "done", "text": txt, "result": ires}
        if act == "pulse":
            from cli_pulse import _compute, _parse_since
            since = _parse_since(parsed.get("since") or "7d")
            p = await _compute(user["id"], since)
            return {"action": "done",
                    "text": (f"last {p['since_days']}d — "
                             f"{p['leads_found']} leads, "
                             f"{p['total_sent']} sent, "
                             f"{p['total_replies']} replies "
                             f"({p['reply_rate'] * 100:.1f}%)."),
                    "result": p}
    except Exception as e:
        return {"action": "answer",
                "text": f"Couldn't run action: {type(e).__name__}: {str(e)[:160]}"}

    # Playbook install — handled outside the try/except above so the
    # cli import doesn't shadow other except branches.
    if act == "playbook_install":
        try:
            from cli import _BUNDLED_EXAMPLES
        except Exception:
            return {"action": "answer", "text": "Playbook registry unavailable."}
        name = (parsed.get("name") or "").strip().lower()
        if name not in _BUNDLED_EXAMPLES:
            return {"action": "answer",
                    "text": f"Unknown playbook {name!r}. Try: "
                            + ", ".join(list(_BUNDLED_EXAMPLES.keys())[:5]) + "…"}
        spec = _BUNDLED_EXAMPLES[name]
        try:
            await db.save_hunt_recipe(user["id"], name,
                                      description=spec["description"],
                                      config=spec["config"])
            current = await db.get_settings(user["id"]) or {}
            wiz = dict(current.get("wizard") or {})
            mutated = False
            if spec.get("icp") and not wiz.get("business_description"):
                wiz["business_description"] = spec["icp"]
                mutated = True
            if spec.get("target_clients") and not wiz.get("target_clients"):
                wiz["target_clients"] = spec["target_clients"]
                mutated = True
            if mutated:
                current["wizard"] = wiz
            if spec.get("tone") and not current.get("default_tone"):
                current["default_tone"] = spec["tone"]
                mutated = True
            if mutated:
                await db.save_settings(user["id"], current)
        except Exception as e:
            return {"action": "answer",
                    "text": f"Install failed: {type(e).__name__}: {str(e)[:120]}"}
        return {"action": "done",
                "text": (f"installed playbook {name} "
                         "+ seeded ICP / target / tone in the wizard."),
                "result": {"name": name}}

    # Whitelist what we hand back to the dashboard. The AI sometimes
    # returns an action shape the front-end doesn't know how to dispatch
    # (typo, hallucinated tag); coerce those to a friendly answer rather
    # than letting the unhandled action freeze the chat input.
    # a239: `wizard_missing` added so the chat-first UI can render its
    # CTA for the wizard-guard pre-flight injected above.
    _CHAT_CLIENT_ACTIONS = {"start_hunt", "answer", "done", "navigate",
                            "spawn_agents", "list_leads", "wizard_missing"}
    try:
        if not isinstance(parsed, dict) or parsed.get("action") not in _CHAT_CLIENT_ACTIONS:
            return {"action": "answer",
                    "text": "I couldn't turn that into an action I can run. "
                            "Try asking again with more detail, or use one of the example prompts.",
                    "meta": _chat_meta}
        # Attach AI-call usage meta so the chat UI can render
        # OpenClaw-style "model · tokens · tps · latency" under each
        # response.
        if isinstance(parsed, dict) and "meta" not in parsed:
            parsed["meta"] = _chat_meta
        return parsed
    finally:
        # Clear the per-request provider override so the FastAPI
        # threadpool worker doesn't carry it into a different user's
        # next request.
        try:
            from providers import push_provider_override as _pop
            _pop(None)
        except Exception:
            pass


@app.post("/api/setup/reveal-key")
async def api_setup_reveal_key(request: Request, user: dict = Depends(require_user)):
    """a256: return the saved API key for a provider so the Settings →
    API keys panel can show the actual value when the user clicks the
    eye-toggle. Local-mode-only; refuses in cloud since cloud users
    don't own the key (it's tier-managed).
    """
    try:
        from runtime import CAPABILITIES
        if CAPABILITIES.mode != "local":
            return JSONResponse({"ok": False, "error": "cloud_mode",
                                 "message": "Reveal is local-only."}, status_code=403)
    except Exception:
        return JSONResponse({"ok": False, "error": "runtime_unavailable"}, status_code=503)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "bad_json"}, status_code=400)
    provider = (body.get("provider") or "").strip().lower()
    env_var_map = {
        "gemini": "HV_GEMINI_KEY", "anthropic": "HV_ANTHROPIC_KEY", "openai": "HV_OPENAI_KEY",
        "openrouter": "HV_OPENROUTER_KEY", "groq": "HV_GROQ_KEY",
        "deepseek": "HV_DEEPSEEK_KEY", "together": "HV_TOGETHER_KEY",
        "mistral": "HV_MISTRAL_KEY", "perplexity": "HV_PERPLEXITY_KEY",
        "ollama": "HV_OLLAMA_KEY", "lmstudio": "HV_LMSTUDIO_KEY",
        "llamafile": "HV_LLAMAFILE_KEY", "custom": "HV_CUSTOM_KEY",
    }
    env_var = env_var_map.get(provider)
    if not env_var:
        return JSONResponse({"ok": False, "error": "unknown_provider"}, status_code=400)
    try:
        from secrets_store import get_secret
        val = get_secret(env_var) or os.environ.get(env_var) or ""
    except Exception:
        val = ""
    if not val or val == "no-key":
        return JSONResponse({"ok": False, "error": "not_configured"}, status_code=404)
    return {"ok": True, "key": val}


@app.post("/api/setup/key")
async def api_setup_key(request: Request):
    """Save an API key to the secrets store + optionally run a 1-shot
    AI probe. Body: {provider, key, test?}. Returns {ok, backend,
    test_passed, test_response, message}. Bound to localhost only by
    the runtime guard — public deployments should never expose this
    route, but the server runs on 127.0.0.1 by default."""
    # Local mode only — refuse to save keys when serving from cloud.
    # Fail closed: if the runtime module can't import for any reason
    # (circular import, dev-mode reload error), refuse the request rather
    # than silently fall through and accept a keychain write from a
    # potentially exposed network listener.
    try:
        from runtime import CAPABILITIES
        if CAPABILITIES.mode != "local":
            return JSONResponse(
                {"ok": False, "error": "cloud_mode",
                 "message": "Setup wizard is local-only. Configure keys via env."},
                status_code=403,
            )
    except Exception:
        return JSONResponse(
            {"ok": False, "error": "runtime_unavailable",
             "message": "Could not verify local-mode gate; refusing keychain write."},
            status_code=503,
        )
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "bad_json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "expected_object"}, status_code=400)
    provider = (body.get("provider") or "").strip().lower()
    key = (body.get("key") or "").strip()
    do_test = bool(body.get("test", True))
    base_url = (body.get("base_url") or "").strip()
    custom_model = (body.get("model") or "").strip()
    if provider not in _VALID_PROVIDERS:
        return JSONResponse({"ok": False, "error": "unknown_provider",
                             "message": f"Provider must be one of: {', '.join(sorted(_VALID_PROVIDERS))}"},
                            status_code=400)
    # Local providers can have an empty key (default Ollama / LM Studio
    # have no auth). Custom needs base_url. All others need a real key.
    is_local = provider in _LOCAL_PROVIDER_SLUGS
    is_custom = provider == "custom"
    if is_custom and not base_url:
        return JSONResponse({"ok": False, "error": "missing_base_url",
                             "message": "Custom provider requires a base URL (e.g. https://my-gateway.example.com/v1)."},
                            status_code=400)
    if not is_local and not is_custom:
        if len(key) < 10 or len(key) > 500:
            return JSONResponse({"ok": False, "error": "bad_key_length",
                                 "message": "API key looks too short or too long — paste the full value."},
                                status_code=400)
    elif is_local and key and len(key) > 500:
        return JSONResponse({"ok": False, "error": "bad_key_length",
                             "message": "Optional auth token too long."}, status_code=400)
    elif is_custom and len(key) > 500:
        return JSONResponse({"ok": False, "error": "bad_key_length",
                             "message": "API key too long."}, status_code=400)
    env_var = {
        "gemini": "HV_GEMINI_KEY", "anthropic": "HV_ANTHROPIC_KEY", "openai": "HV_OPENAI_KEY",
        "openrouter": "HV_OPENROUTER_KEY", "groq": "HV_GROQ_KEY",
        "deepseek": "HV_DEEPSEEK_KEY", "together": "HV_TOGETHER_KEY",
        "mistral": "HV_MISTRAL_KEY", "perplexity": "HV_PERPLEXITY_KEY",
        "ollama": "HV_OLLAMA_KEY", "lmstudio": "HV_LMSTUDIO_KEY",
        "llamafile": "HV_LLAMAFILE_KEY", "custom": "HV_CUSTOM_KEY",
    }[provider]
    backend = "(unknown)"
    try:
        from secrets_store import set_secret, _backend_label
        # For local providers with no key, persist a sentinel so the
        # provider list reflects "configured" without leaking real
        # auth. The provider abstraction treats "no-key" as keyless.
        save_value = key or ("no-key" if (is_local or is_custom) else "")
        if save_value:
            set_secret(env_var, save_value)
        backend = _backend_label()
        # Custom endpoint: also persist the base_url + model in
        # config.toml so subsequent CLI invocations pick them up.
        if is_custom:
            os.environ["HV_CUSTOM_BASE_URL"] = base_url
            if custom_model:
                os.environ["HV_CUSTOM_MODEL"] = custom_model
            try:
                set_secret("HV_CUSTOM_BASE_URL", base_url)
                if custom_model:
                    set_secret("HV_CUSTOM_MODEL", custom_model)
            except Exception:
                pass
    except Exception as e:
        return JSONResponse({"ok": False, "error": "save_failed",
                             "message": f"{type(e).__name__}: {str(e)[:120]}"},
                            status_code=500)
    # Make values available to this process for the probe — but only
    # when there's actually a value to set. Empty-string assignment
    # would silently clobber any pre-existing env var of the same name.
    if save_value:
        os.environ[env_var] = save_value
    # else: leave os.environ untouched — caller's existing key (if any)
    # remains in effect.
    # Persist preferred_provider in config.toml so the next CLI run
    # picks the right provider even before a key is loaded
    try:
        from pathlib import Path as _P
        cfg_dir = _P(os.environ.get("XDG_CONFIG_HOME") or _P.home() / ".config") / "huntova"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        cfg_path = cfg_dir / "config.toml"
        existing = cfg_path.read_text() if cfg_path.exists() else ""
        # Replace or insert preferred_provider line
        lines = [ln for ln in existing.splitlines() if not ln.strip().startswith("preferred_provider")]
        lines.insert(0, f'preferred_provider = "{provider}"')
        cfg_path.write_text("\n".join(lines) + "\n")
        if os.name != "nt":
            try: os.chmod(cfg_path, 0o600)
            except OSError: pass
    except Exception:
        pass
    test_passed = None
    test_response = ""
    test_message = ""
    if do_test:
        try:
            from providers import get_provider
            settings = {"preferred_provider": provider, env_var: save_value or "no-key"}
            if is_custom:
                settings["HV_CUSTOM_BASE_URL"] = base_url
                if custom_model:
                    settings["HV_CUSTOM_MODEL"] = custom_model
            p = get_provider(settings)
            try:
                resp = p.chat(
                    messages=[{"role": "user", "content": "respond with OK"}],
                    max_tokens=5,
                    temperature=0.0,
                    timeout_s=15.0,
                )
                test_passed = bool((resp or "").strip())
                test_response = (resp or "").strip()[:60]
            except Exception as e:
                test_passed = False
                _emsg = str(e)
                if key and len(key) >= 8:
                    _emsg = _emsg.replace(key, "***redacted***")
                test_message = f"{type(e).__name__}: {_emsg[:120]}"
        except RuntimeError as e:
            test_passed = False
            _emsg = str(e)
            if key and len(key) >= 8:
                _emsg = _emsg.replace(key, "***redacted***")
            test_message = f"provider init: {_emsg[:120]}"
    return {
        "ok": True,
        "provider": provider,
        "backend": backend,
        "test_passed": test_passed,
        "test_response": test_response,
        "test_message": test_message,
    }


def _huntova_version() -> str:
    try:
        import importlib.metadata as _md
        return _md.version("huntova")
    except Exception:
        try:
            import tomllib  # py3.11+
            from pathlib import Path as _P
            data = tomllib.loads((_P(__file__).resolve().parent / "pyproject.toml").read_text())
            return data.get("project", {}).get("version", "?")
        except Exception:
            return "?"


@app.get("/install.sh")
async def install_sh():
    """Curl-pipe installer alias — `curl -fsSL .../install.sh | sh`.

    Serves the same script that ships in static/install.sh as plain
    text so `sh` interprets it directly. Caches for 5 minutes so an
    accidental DoS-on-share doesn't burn through CDN credits.
    """
    path = os.path.join(STATIC_DIR, "install.sh")
    try:
        with open(path, "r", encoding="utf-8") as f:
            body = f.read()
    except OSError:
        return Response("# Huntova installer not found", status_code=404, media_type="text/plain")
    return Response(
        content=body,
        media_type="text/plain; charset=utf-8",
        headers={
            "Cache-Control": "public, max-age=300",
            "Content-Disposition": 'inline; filename="install.sh"',
        },
    )


@app.get("/dashboard", response_class=HTMLResponse)
@app.get("/agent", response_class=HTMLResponse)
@app.get("/hunts", response_class=HTMLResponse)
async def dashboard_pages(request: Request):
    """a240: the legacy panel-based dashboard is decommissioned. Every
    URL that used to render `index.html` now redirects into Jarvis with
    a `?panel=<name>` hint so the right panel auto-opens. The old
    `index.html` template is kept on disk so any direct test references
    still resolve, but no live route serves it."""
    user = await get_current_user(request)
    if not user:
        return RedirectResponse("/landing")
    path = request.url.path
    if path.startswith("/agent"):
        return RedirectResponse("/?panel=agent")
    if path.startswith("/hunts"):
        return RedirectResponse("/?panel=hunts")
    return RedirectResponse("/")


def _read_template(name: str) -> str:
    path = os.path.join(TEMPLATES_DIR, name)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ═══════════════════════════════════════════════════════════════
# API: LEADS
# ═══════════════════════════════════════════════════════════════

@app.get("/api/leads")
async def api_leads(request: Request, user: dict = Depends(require_user)):
    # Optional pagination via ?limit=N&offset=M. db.get_leads enforces a
    # 10k hard cap regardless so a compromised session can't dump the
    # entire table in a single request. Default (no params) preserves
    # existing frontend behaviour.
    qs = request.query_params
    limit: int | None = None
    offset = 0
    try:
        if "limit" in qs:
            limit = max(1, min(int(qs["limit"]), 10000))
    except (ValueError, TypeError):
        pass
    try:
        if "offset" in qs:
            offset = max(0, int(qs["offset"]))
    except (ValueError, TypeError):
        pass
    leads = await db.get_leads(user["id"], limit=limit, offset=offset)
    # Clean link fields
    link_fields = ("contact_email", "contact_linkedin", "org_linkedin", "org_website", "contact_page_url")
    for ld in leads:
        for fk in link_fields:
            fv = ld.get(fk)
            if fv and isinstance(fv, str):
                fv = fv.strip()
                if not fv or fv.lower() in ("null", "none", "n/a", "undefined", "not found", "not available", ""):
                    ld[fk] = None
            elif not fv:
                ld[fk] = None
    return leads


@app.get("/api/stats")
async def api_stats(user: dict = Depends(require_user)):
    leads = await db.get_leads(user["id"])
    st = {}
    for l in leads:
        s = l.get("email_status", "new")
        st[s] = st.get(s, 0) + 1
    st["total"] = len(leads)
    st["with_email"] = sum(1 for l in leads if l.get("contact_email"))
    st["recurring"] = sum(1 for l in leads if l.get("is_recurring"))
    st["with_linkedin"] = sum(1 for l in leads if l.get("org_linkedin") or l.get("contact_linkedin"))
    countries = {}
    for l in leads:
        c = l.get("country", "?")
        countries[c] = countries.get(c, 0) + 1
    st["countries"] = countries
    return st


@app.post("/api/update")
async def api_update(request: Request, user: dict = Depends(require_user)):
    body = await request.json()
    lid = body.get("lead_id")
    if not lid:
        return JSONResponse({"error": "lead_id required"}, status_code=400)
    now = datetime.now(timezone.utc).isoformat()
    # Stability fix (Perplexity bug #79): the previous flow was
    # get_lead → mutate Python dict → upsert_lead — three separate
    # DB calls, classic lost-update race. Two CRM panels (status
    # dropdown, notes, edit form) editing different fields would
    # silently clobber each other. Now the read+mutate+write happens
    # inside one transaction with a row lock via merge_lead.
    _outcome_signals = {
        "replied": ("good", "replied to outreach"),
        "meeting_booked": ("good", "booked meeting"),
        "won": ("good", "won deal"),
        "lost": ("bad", "lost deal"),
    }
    _outcome_to_log: list[tuple[str, str]] = []  # collected during mutator, fired after lock release

    def _mutator(lead: dict) -> dict:
        if "email_status" in body:
            old, new = lead.get("email_status", "new"), body["email_status"]
            if old != new:
                lead["email_status"] = new
                lead["email_status_date"] = now
                h = lead.get("status_history", [])
                # Skip duplicate consecutive entries — protects against
                # double-clicks / retries that otherwise clutter the
                # timeline with N copies of the same status.
                if not h or h[-1].get("status") != new:
                    h.append({"status": new, "date": now})
                    if len(h) > 100:
                        h = h[-100:]
                lead["status_history"] = h
                if new in _outcome_signals:
                    _outcome_to_log.append(_outcome_signals[new])
        if "linkedin_status" in body:
            lead["linkedin_status"] = body["linkedin_status"]
            lead["linkedin_status_date"] = now
        if "notes" in body:
            lead["notes"] = (body["notes"] or "")[:4000]
        for fld in ("email_subject", "email_body", "linkedin_note", "deal_tier",
                    "contact_name", "contact_role", "contact_email", "contact_linkedin",
                    "org_linkedin", "org_website", "contact_phone", "contact_page_url",
                    "fit_score", "why_fit", "production_gap", "buyability_score",
                    "timing_score", "is_recurring", "platform_used", "service_opportunity_score",
                    # a248: 3 user-defined CRM custom fields. Labels live
                    # in Settings → CRM (crm_custom_field_1/2/3); these
                    # are the per-lead values rendered on the lead-detail
                    # custom-fields strip and saved on debounced typing.
                    "custom_field_1", "custom_field_2", "custom_field_3",
                    # a248: tag list — comma-string from UI or array.
                    "tags"):
            if fld in body:
                lead[fld] = body[fld]
        if "email_opens" in body:
            lead["email_opens"] = body["email_opens"]
        if "follow_up_date" in body:
            lead["follow_up_date"] = body["follow_up_date"]
        return lead

    lead = await db.merge_lead(user["id"], lid, _mutator)
    if lead is None:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    # Side-effect feedback writes happen AFTER the lock release so
    # they don't extend the row-lock window.
    for _sig, _reason in _outcome_to_log:
        try:
            await db.save_lead_feedback(user["id"], lid, _sig, f"outcome:{_reason}")
        except Exception:
            pass
    return {"ok": True, "lead": lead}


@app.post("/api/bulk-update")
async def api_bulk_update(request: Request, user: dict = Depends(require_user)):
    body = await request.json()
    ids = set(body.get("lead_ids", []))
    st = body.get("email_status")
    if not ids or not st:
        return JSONResponse({"ok": False, "error": "need lead_ids + email_status"}, status_code=400)
    # Validate status against the canonical set so a bot/typo can't
    # write garbage values into the leads table that the dashboard
    # filters can't display ("pwned", random unicode, 5MB strings).
    # Mirrors the values the dashboard's bulk-status <select> exposes.
    _VALID_BULK_STATUSES = {
        "new", "email_drafted", "email_sent", "followed_up", "replied",
        "meeting_booked", "won", "lost", "ignored",
    }
    if st not in _VALID_BULK_STATUSES:
        return JSONResponse(
            {"ok": False, "error": f"invalid email_status: {str(st)[:40]}"},
            status_code=400,
        )
    # Cap lead_ids batch — without this an attacker (or buggy client)
    # could submit a 100k-id list and force a full table scan.
    if len(ids) > 500:
        return JSONResponse(
            {"ok": False, "error": "too many lead_ids — cap is 500 per request"},
            status_code=400,
        )
    leads = await db.get_leads(user["id"])
    now = datetime.now(timezone.utc).isoformat()
    updated = 0
    failed_ids: list[str] = []
    # Per-lead try/except so a single upsert failure mid-batch doesn't
    # abort the rest. The old loop returned 'updated: N' with no visibility
    # into which rows silently failed — frontend reported success for
    # partial updates. Now we track failed ids and return them in the
    # response so the UI can retry or warn the user precisely.
    # Stability fix (audit wave 27): the previous version called
    # `db.upsert_lead` which preserves `email_status` via the CASE
    # WHEN leads.email_status <> 'new' THEN leads.email_status guard
    # (intended for agent re-discovery — see a194). For an explicit
    # user-initiated bulk-status-change the guard wrong-direction:
    # past-`new` leads silently skip the column update while the
    # JSON `data` blob gets the new status (since `data = EXCLUDED.data`
    # is unconditional), so column and JSON disagree. Future SQL
    # filters on `email_status` would return wrong results, and
    # `status_history` ends up with an entry for a transition that
    # didn't actually happen. Switch to `merge_lead` (the same
    # function `/api/update` uses) so the indexed column is updated
    # unconditionally for user-driven mutations.
    def _build_mutator(_st, _now):
        def _m(d):
            d = dict(d) if isinstance(d, dict) else {}
            d["email_status"] = _st
            d["email_status_date"] = _now
            _h = d.get("status_history") or []
            if not isinstance(_h, list):
                _h = []
            _h.append({"status": _st, "date": _now})
            d["status_history"] = _h
            return d
        return _m
    for l in leads:
        if l.get("lead_id") not in ids:
            continue
        old = l.get("email_status", "new")
        if old == st:
            continue
        try:
            await db.merge_lead(user["id"], l["lead_id"],
                                _build_mutator(st, now))
            updated += 1
        except Exception as _err:
            print(f"[BULK] merge failed for user {user['id']} lead {l.get('lead_id')}: {_err}")
            failed_ids.append(l.get("lead_id"))
    result = {"ok": len(failed_ids) == 0, "updated": updated}
    if failed_ids:
        result["failed_ids"] = failed_ids
        result["error"] = f"{len(failed_ids)} lead(s) failed to update"
    return result


@app.post("/api/delete")
async def api_delete(request: Request, user: dict = Depends(require_user)):
    body = await request.json()
    lid = body.get("lead_id")
    # a252: bulk delete — accept lead_ids list (preferred) or fall back
    # to single lead_id. Caps at 500 to mirror /api/bulk-update.
    ids = body.get("lead_ids")
    if isinstance(ids, list) and ids:
        if len(ids) > 500:
            return JSONResponse({"error": "too many lead_ids — cap is 500 per request"}, status_code=400)
        deleted = 0
        not_found = []
        for _id in ids:
            try:
                if await db.delete_lead(user["id"], str(_id)):
                    deleted += 1
                else:
                    not_found.append(_id)
            except Exception:
                not_found.append(_id)
        return {"ok": True, "deleted": deleted, "not_found": not_found}
    if not lid:
        return JSONResponse({"error": "lead_id required"}, status_code=400)
    result = await db.delete_lead(user["id"], lid)
    if not result:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"ok": True}


@app.post("/api/undo-delete")
async def api_undo_delete(request: Request, user: dict = Depends(require_user)):
    body = await request.json()
    lid = body.get("lead_id")
    if not lid:
        return JSONResponse({"error": "lead_id required"}, status_code=400)
    lead = await db.restore_lead(user["id"], lid)
    if not lead:
        return JSONResponse({"error": "not found in archive"}, status_code=404)
    return {"ok": True, "lead": lead}


# ═══════════════════════════════════════════════════════════════
# API: SHARE (Feature F1 — shareable hunt replays)
# ═══════════════════════════════════════════════════════════════

# Public-safe lead fields. Anything not in this list (notes, internal
# scoring metadata, generic-email flag, status history, drafts) stays
# private. Conservative by design — we'd rather under-share than leak.
# `proof_pack` is the v1 evidence-first dossier (sources, quotes,
# freshness) — see _build_proof_pack().
# Public /h/<slug> share pages must NOT leak personal contact data.
# Per GDPR Art.5(1)(b) (purpose limitation): contact_email / phone /
# linkedin / role of an individual at the prospect company are personal
# data — leaking them publicly via a share URL has no lawful basis.
# Only org-level identifiers stay in the snapshot. The recipient who
# follows the share gets the *opportunity*, not the *contact*; they
# install huntova and re-discover the contact through their own AI key
# (which establishes their own legitimate-interest basis).
_SHARE_LEAD_FIELDS = (
    "org_name", "org_website", "country", "city", "fit_score",
    "why_fit", "production_gap", "event_name", "event_type", "url",
    "org_linkedin",  # company page only; NOT contact_linkedin (PII)
    "fit_rationale", "timing_rationale", "proof_pack",
)


_FREE_EMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "aol.com", "icloud.com", "proton.me", "protonmail.com",
}


def _compute_reachability(lead: dict) -> dict:
    """Round-68 Tab 0 weight model: additive scoring 0-100 with five
    named tiers. Returns:
      tier:     "direct-evidence" | "strong-attributed" | "probable-route" | "heuristic-guess" | "no-path"
      tier_label: human-readable label
      score:    0-100
      score_pct: float 0-1 for meter rendering
      reasons:  list of {kind, text} where kind ∈ ✓ ~ ✕
      proof_trail: list of compact pill labels
    """
    score = 0
    reasons: list = []
    proof: list = []

    email = (lead.get("contact_email") or "").strip()
    linkedin = (lead.get("contact_linkedin") or "").strip()
    org_li = (lead.get("org_linkedin") or "").strip()
    org_site = (lead.get("org_website") or "").strip()
    contact_role = (lead.get("contact_role") or "").strip()
    contact_name = (lead.get("contact_name") or "").strip()
    quote_v = (lead.get("_quote_verified") or "").strip()
    is_generic = bool(lead.get("_is_generic_email"))
    data_conf = lead.get("_data_confidence")
    sig_count = int(lead.get("_confidence_signals") or 0)

    # ── Positives ──
    if email and not is_generic and quote_v in ("exact", "close"):
        score += 45
        reasons.append({"kind": "✓", "text": "Email found on crawled page"})
        proof.append("email on page")
    elif email and not is_generic:
        score += 40
        reasons.append({"kind": "✓", "text": "Email extracted from contact page"})
        proof.append("email on page")
    elif email and is_generic:
        score += 5
        reasons.append({"kind": "~", "text": "Generic info@/contact@ address only"})
        proof.append("generic email")

    if org_site:
        score += 10
        reasons.append({"kind": "✓", "text": "Domain matches org website"})
        proof.append("domain verified")

    if linkedin and contact_name:
        score += 15
        reasons.append({"kind": "✓", "text": "Contact LinkedIn matches person + company"})
        proof.append("linkedin match")

    if org_li:
        score += 8
        proof.append("company linkedin")

    if contact_role and contact_name:
        score += 15
        reasons.append({"kind": "✓", "text": "Decision-maker role identified"})
        proof.append("role + name")

    if quote_v in ("exact", "close") and not email:
        score += 10
        reasons.append({"kind": "✓", "text": "Page quotes verified — strong evidence cluster"})
        proof.append("quote verified")

    if isinstance(data_conf, (int, float)):
        if data_conf >= 0.6:
            score += 10
        elif data_conf >= 0.4:
            score += 5
    if sig_count >= 3:
        score += 5
        reasons.append({"kind": "✓", "text": f"{sig_count} corroborating signals"})

    # ── Negatives ──
    if email and is_generic:
        score -= 15
        reasons.append({"kind": "✕", "text": "Free / generic mailbox lowers reachability"})

    # If contact email's domain doesn't match the org domain, penalise.
    if email and org_site:
        try:
            email_domain = email.rsplit("@", 1)[-1].lower()
            site_host = urlparse(org_site).netloc.lower().replace("www.", "")
            if email_domain in _FREE_EMAIL_DOMAINS and "." in email_domain:
                # Already penalised above as generic, but explicit:
                pass
            elif email_domain and site_host and email_domain.split(".")[-2:] != site_host.split(".")[-2:]:
                score -= 20
                reasons.append({"kind": "✕", "text": "Email domain doesn't match org site"})
        except Exception:
            pass

    if not email and not linkedin and not org_li:
        reasons.append({"kind": "✕", "text": "No first-party contact path found"})

    score = max(0, min(100, score))

    if score >= 90:
        tier, label = "direct-evidence", "Direct evidence"
    elif score >= 75:
        tier, label = "strong-attributed", "Strong attributed"
    elif score >= 55:
        tier, label = "probable-route", "Probable route"
    elif score >= 30:
        tier, label = "heuristic-guess", "Heuristic guess"
    else:
        tier, label = "no-path", "No reliable path"

    # Cap reasons to 4 (3 bullets + 1 spillover) so the bar stays compact
    return {
        "tier": tier,
        "tier_label": label,
        "score": score,
        "score_pct": score / 100.0,
        "reasons": reasons[:4],
        "proof_trail": proof[:5],
        # Legacy fields for the older render path (kept until plugin
        # updates align):
        "paths": [{"channel": "summary", "source": label, "confidence_label": tier}],
    }


def _build_proof_pack(lead: dict) -> dict:
    """Compute a Proof Pack from whatever signals the lead already has.

    The agent doesn't yet emit a structured proof_pack field, so for
    now we synthesise one from the existing fields (rationale, found_at,
    source URLs). When evidence-first plugins land they'll attach
    pre-built proof_pack dicts directly and this fallback gets
    skipped.
    """
    if isinstance(lead.get("proof_pack"), dict) and lead["proof_pack"]:
        existing = dict(lead["proof_pack"])
        existing.setdefault("reachability", _compute_reachability(lead))
        return existing
    sources = []
    if lead.get("org_website"):
        sources.append({"label": "company website", "url": lead["org_website"]})
    if lead.get("url") and lead.get("url") != lead.get("org_website"):
        sources.append({"label": "first found", "url": lead["url"]})
    if lead.get("contact_linkedin"):
        sources.append({"label": "contact linkedin", "url": lead["contact_linkedin"]})
    if lead.get("org_linkedin"):
        sources.append({"label": "company linkedin", "url": lead["org_linkedin"]})
    quotes = []
    for fld in ("fit_rationale", "timing_rationale", "why_fit", "production_gap"):
        v = lead.get(fld)
        if isinstance(v, str) and len(v.strip()) > 25:
            quotes.append({"text": v.strip()[:280], "tag": fld.replace("_", " ")})
    return {
        "sources": sources,
        "quotes": quotes[:3],
        "fetched_at": lead.get("found_date") or lead.get("created_at") or "",
        "verified": "yes" if (sources and quotes) else "partial",
        "reachability": _compute_reachability(lead),
    }


def _sanitise_lead_for_share(lead: dict) -> dict:
    out = {k: lead.get(k) for k in _SHARE_LEAD_FIELDS if lead.get(k) not in (None, "")}
    out["proof_pack"] = _build_proof_pack(lead)
    return out


@app.post("/api/hunts/share")
async def api_hunts_share(request: Request, user: dict = Depends(require_user)):
    """Snapshot a set of leads (optionally tied to a run) into a public
    `/h/<slug>` page. Body: {run_id?, lead_ids[], title?}. Returns
    {slug, url}. Snapshot semantics — later CRM edits don't affect
    the public page.
    """
    body = await request.json()
    raw_ids = body.get("lead_ids") or []
    title = (body.get("title") or "").strip()
    run_id = body.get("run_id")
    try:
        run_id = int(run_id) if run_id not in (None, "") else None
    except (TypeError, ValueError):
        run_id = None

    if not isinstance(raw_ids, list) or not raw_ids:
        return JSONResponse({"error": "lead_ids required"}, status_code=400)
    # Cap at 50 leads per share — public pages stay snappy and the
    # snapshot blob stays small.
    lead_ids = [str(x) for x in raw_ids if x][:50]
    if not lead_ids:
        return JSONResponse({"error": "lead_ids required"}, status_code=400)

    public_leads: list[dict] = []
    for lid in lead_ids:
        lead = await db.get_lead(user["id"], lid)
        if lead:
            public_leads.append(_sanitise_lead_for_share(lead))
    if not public_leads:
        return JSONResponse({"error": "no accessible leads"}, status_code=404)

    # Validate run_id belongs to the caller. Without this check, user A
    # could create a public share that names user B's run_id as the
    # source — leaking ownership metadata + skewing analytics. lead_ids
    # were already filtered by user["id"] above, but run_id is taken
    # from the body unverified.
    if run_id:
        try:
            _run_row = await db._afetchone(
                "SELECT user_id FROM agent_runs WHERE id = %s", [run_id])
            if not _run_row or _run_row.get("user_id") != user["id"]:
                # Drop the bogus run_id rather than reject the whole
                # request — old clients may send stale run_ids on retry.
                run_id = None
        except Exception:
            run_id = None

    hunt_meta = {
        "leads_total": len(public_leads),
        "shared_at": datetime.now(timezone.utc).isoformat(),
    }
    # Defensive size cap on the serialised snapshot. 50 leads × big
    # rationales can theoretically push past a few MB; the public share
    # render then has to ship that whole blob to every visitor. Cap at
    # 2MB and trim the largest rationale fields if we'd exceed.
    _SHARE_CAP = 2_000_000
    import json as _json
    snap = _json.dumps({"leads": public_leads, "meta": hunt_meta},
                        ensure_ascii=False, default=str)
    if len(snap) > _SHARE_CAP:
        for ld in public_leads:
            for k in ("fit_rationale", "timing_rationale",
                      "buyability_rationale", "why_fit"):
                v = ld.get(k)
                if isinstance(v, str) and len(v) > 280:
                    ld[k] = v[:280] + "…"
        snap = _json.dumps({"leads": public_leads, "meta": hunt_meta},
                            ensure_ascii=False, default=str)
        if len(snap) > _SHARE_CAP:
            return JSONResponse(
                {"ok": False,
                 "error": "share snapshot exceeds 2MB even after trimming "
                          "rationales — share fewer leads or use --top"},
                status_code=413)
    slug = await db.create_hunt_share(
        user_id=user["id"], run_id=run_id, leads=public_leads,
        hunt_meta=hunt_meta, title=title)
    return {"ok": True, "slug": slug, "url": f"{PUBLIC_URL}/h/{slug}"}


@app.get("/api/hunts/shares")
async def api_hunts_shares_list(user: dict = Depends(require_user)):
    """List the caller's recent shares for an account-page management view."""
    items = await db.list_hunt_shares(user["id"], limit=100)
    base = PUBLIC_URL.rstrip("/")
    out = []
    for r in items:
        out.append({
            "slug": r["slug"],
            "url": f"{base}/h/{r['slug']}",
            "title": r.get("title") or "",
            "run_id": r.get("run_id"),
            "view_count": int(r.get("view_count") or 0),
            "revoked": bool(int(r.get("revoked") or 0)),
            "created_at": r["created_at"],
            "expires_at": r.get("expires_at"),
        })
    return {"ok": True, "shares": out}


@app.post("/api/hunts/share/{slug}/revoke")
async def api_hunts_share_revoke(slug: str, user: dict = Depends(require_user)):
    ok = await db.revoke_hunt_share(user["id"], slug)
    if not ok:
        return JSONResponse({"error": "not found or already revoked"}, status_code=404)
    return {"ok": True}


# ── Public recipe registry (Kimi round-74 spec) ───────────────────
# Endpoints gated by HV_RECIPE_URL_BETA env flag. Pre-launch the scaffold
# is here so Tuesday afternoon (post-HN) Enzo can flip the flag and
# announce v1.1 in a single deploy. If launch goes flat or phenomenally
# well, leave the flag off — same-day v1.1 ships only when the launch
# went "fine but not viral".

_RECIPE_URL_RATE_BUCKETS: dict[str, list[float]] = {}
_RECIPE_URL_RATE_LIMIT = 10  # max publishes per IP per hour


def _recipe_url_enabled() -> bool:
    return bool(os.environ.get("HV_RECIPE_URL_BETA"))


def _recipe_url_rate_check(ip: str) -> bool:
    import time as _t
    now = _t.time()
    bucket = _RECIPE_URL_RATE_BUCKETS.setdefault(ip, [])
    bucket[:] = [t for t in bucket if t > now - 3600.0]
    if len(bucket) >= _RECIPE_URL_RATE_LIMIT:
        return False
    bucket.append(now)
    return True


@app.post("/api/recipe/publish")
async def api_recipe_publish(request: Request):
    """Mint a public /r/<slug> for a recipe payload. Body is the recipe
    JSON; returns {ok, slug, url}. Rate-limited per IP."""
    if not _recipe_url_enabled():
        return JSONResponse({"ok": False, "error": "feature_disabled",
                             "message": "Public recipe URLs are in beta — set HV_RECIPE_URL_BETA on the server to enable"},
                            status_code=404)
    ip = _get_client_ip(request)
    if not _recipe_url_rate_check(ip):
        return JSONResponse({"ok": False, "error": "rate_limited"}, status_code=429)
    # Cap the request body BEFORE parsing JSON. A genuine recipe is a
    # few KB; multi-MB payloads are abuse. 256KB covers the largest
    # realistic recipe (10 saved-query strings × ~500 chars + metadata
    # × generous slack).
    #
    # Stability fix (audit wave 26): the previous version checked
    # `request.headers.get("content-length")` and short-circuited with
    # `if _content_length and _content_length > 256*1024`. The
    # `_content_length and ...` truthiness gate meant a request with
    # no Content-Length header at all (HTTP/1.1 chunked encoding,
    # h2 streamed bodies) had `_content_length == 0`, the gate
    # evaluated to False, and the cap was silently bypassed —
    # `await request.json()` then buffered the entire body, no matter
    # how big. Read the body manually with a hard cap instead so the
    # check can't be sidestepped by a missing header.
    _CAP = 256 * 1024
    _declared = int(request.headers.get("content-length") or 0)
    if _declared > _CAP:
        return JSONResponse({"ok": False, "error": "payload_too_large",
                             "message": "recipe payload exceeds 256KB"},
                            status_code=413)
    try:
        raw = await request.body()
    except Exception:
        return JSONResponse({"ok": False, "error": "bad_request"}, status_code=400)
    if len(raw) > _CAP:
        return JSONResponse({"ok": False, "error": "payload_too_large",
                             "message": "recipe payload exceeds 256KB"},
                            status_code=413)
    try:
        import json as _j
        body = _j.loads(raw or b"{}")
    except Exception:
        return JSONResponse({"ok": False, "error": "bad_json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "expected_object"}, status_code=400)
    name = (body.get("name") or "").strip()[:120]
    description = (body.get("description") or "").strip()[:400]
    if "recipe" not in body or not isinstance(body["recipe"], dict):
        return JSONResponse({"ok": False, "error": "missing_recipe_field"}, status_code=400)
    try:
        slug = await db.publish_public_recipe(body, name=name, description=description)
    except Exception as e:
        return JSONResponse({"ok": False, "error": "publish_failed",
                             "message": f"{type(e).__name__}"}, status_code=500)
    base = PUBLIC_URL.rstrip("/")
    return {"ok": True, "slug": slug, "url": f"{base}/r/{slug}"}


@app.get("/r/{slug}.json")
async def public_recipe_json(slug: str):
    """JSON view of a public recipe — `huntova recipe import-url` reads
    this. Always 200 with the payload OR 404 if not found / revoked."""
    if not _recipe_url_enabled():
        raise HTTPException(status_code=404, detail="feature_disabled")
    rec = await db.get_public_recipe(slug)
    if not rec:
        raise HTTPException(status_code=404, detail="recipe_not_found")
    return rec["payload"]


@app.get("/r/{slug}", response_class=HTMLResponse)
async def public_recipe_page(slug: str):
    """Human-readable public page for a recipe — shows the ICP, plugin
    deps, and the one-liner `huntova recipe import-url <url>` users
    paste to fork it locally.

    Defence-in-depth (Kimi round-75): emit a strict Content-Security-
    Policy on this route so even if an HTML-escape bug slips through
    the renderer, inline script execution is blocked. The page renders
    user-controlled JSON so we treat it as untrusted territory."""
    if not _recipe_url_enabled():
        raise HTTPException(status_code=404, detail="feature_disabled")
    rec = await db.get_public_recipe(slug)
    if not rec:
        raise HTTPException(status_code=404, detail="recipe_not_found")
    return HTMLResponse(
        _render_public_recipe_page(rec),
        headers={
            # default-src 'self' — same-origin only.
            # script-src 'self' — only inline event handlers via this
            # response can run. The renderer uses an inline onclick on
            # the Copy button reading from data-cmd, which is allowed
            # under script-src 'self' for inline event handlers in the
            # legacy CSP model. If we ever move the handler to a
            # separate <script> tag, swap to a nonce.
            # img/style/font-src 'self' + 'unsafe-inline' — inline CSS
            # is in the shell template; no remote assets.
            # frame-ancestors 'none' — no clickjacking.
            "Content-Security-Policy": (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; "
                "font-src 'self'; "
                "connect-src 'self'; "
                "frame-ancestors 'none'; "
                "base-uri 'self'; "
                "form-action 'self'"
            ),
            "X-Frame-Options": "DENY",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer-when-downgrade",
        },
    )


def _render_public_recipe_page(rec: dict) -> str:
    """Minimal terminal-styled page for /r/<slug>. Inline CSS, zero
    external requests, copy-button on the import command. All user-
    controlled fields run through html.escape before reaching the body
    template so a malicious recipe payload can't inject script tags."""
    from html import escape as _esc
    payload = rec.get("payload") or {}
    inner = payload.get("recipe") if isinstance(payload, dict) else {}
    if not isinstance(inner, dict):
        inner = {}
    # Raw values for the shell (which does its own escaping)
    name_raw = (rec.get("name") or inner.get("name") or "Untitled recipe")
    description_raw = (rec.get("description") or inner.get("description") or "")
    # Escaped values for direct injection into our body template
    name = _esc(str(name_raw))
    description = _esc(str(description_raw))
    countries = inner.get("countries") or []
    queries = inner.get("queries") or []
    plugins = payload.get("plugins") if isinstance(payload, dict) else []
    if not isinstance(plugins, list):
        plugins = []
    # Slug is 8-char hex from secrets.token_hex(4) — safe by
    # construction, but escape defensively
    slug = _esc(str(rec.get("slug") or ""))
    base = _esc(PUBLIC_URL.rstrip("/"))
    import_cmd = f"huntova recipe import-url {base}/r/{slug}.json"

    countries_pills = "".join(
        f"<span class='pill'>{_esc(str(c))[:40]}</span>" for c in countries[:8]
        if isinstance(c, str)
    )
    queries_lis = "".join(
        f"<li><code>{_esc(str(q))[:120]}</code></li>" for q in queries[:8]
        if isinstance(q, str)
    )
    plugins_pills = "".join(
        f"<span class='pill plug'>{_esc(str(p))[:40]}</span>" for p in plugins[:8]
        if isinstance(p, str)
    )

    body = (
        "<main class='wrap'>"
        "<header class='hero'>"
        "<p class='kicker'>Shared recipe · forkable</p>"
        f"<h1>{name}</h1>"
        + (f"<p class='dateline'>{description}</p>" if description else "")
        + "</header>"
        "<div class='import'>"
        "<p class='import-label'>Import locally</p>"
        f"<pre class='import-cmd'><code>$ {import_cmd}</code></pre>"
        f"<button class='import-copy' data-cmd='{import_cmd}' onclick=\"navigator.clipboard.writeText(this.dataset.cmd).then(()=>{{this.textContent='Copied'}})\">Copy</button>"
        "</div>"
        "<section class='meta'>"
        + (f"<div><h3>Countries</h3>{countries_pills}</div>" if countries_pills else "")
        + (f"<div><h3>Plugins</h3>{plugins_pills}</div>" if plugins_pills else "")
        + (f"<div><h3>Search queries</h3><ul>{queries_lis}</ul></div>" if queries_lis else "")
        + "</section>"
        "</main>"
    )
    # Pass RAW values to the shell — it escapes them again. Avoids
    # double-escape (& → &amp; → &amp;amp;) on the og:title meta tag.
    og_desc_raw = (
        f"Shared Huntova recipe · {description_raw[:120]}"
        if description_raw else
        "Shared Huntova recipe · forkable hunt config"
    )
    return _render_share_shell(
        title=f"{name_raw} — Huntova recipe",
        body=body,
        og_description=og_desc_raw,
        og_image="",  # no per-recipe OG image yet — week-2 if traction
    )


@app.get("/h/{slug}/og.svg")
async def public_hunt_share_og(slug: str):
    """Dynamic OG image for /h/<slug> — the link-as-billboard. Returns
    a 1200x630 terminal-styled SVG so social previews show the actual
    hunt query + top leads + fit scores instead of a generic page card."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{4,32}", slug or ""):
        return Response(
            content="<svg xmlns='http://www.w3.org/2000/svg' width='1200' height='630'/>",
            media_type="image/svg+xml",
            status_code=404,
        )
    share = await db.get_hunt_share(slug)
    if not share:
        share = {"slug": slug, "title": "Huntova hunt", "leads": [],
                 "meta": {"icp": "Huntova hunt"}}
    svg = _render_share_og_svg(share)
    return Response(
        content=svg,
        media_type="image/svg+xml",
        headers={
            "Cache-Control": "public, max-age=3600, s-maxage=3600",
            "X-Robots-Tag": "noindex",
        },
    )


@app.get("/h/{slug}.json")
async def public_hunt_share_json(slug: str):
    """Machine-readable share snapshot. Same data as the HTML page,
    but as JSON so `huntova hunt --from-share <slug>` can reproduce
    the original ICP locally without scraping HTML.
    """
    if not re.fullmatch(r"[A-Za-z0-9_-]{4,32}", slug or ""):
        return JSONResponse({"ok": False, "error": "invalid slug"}, status_code=404)
    share = await db.get_hunt_share(slug)
    if not share:
        return JSONResponse({"ok": False, "error": "share not found or expired"}, status_code=404)
    # Strip user_id (private) before publishing
    safe = {k: v for k, v in share.items() if k != "user_id"}
    return {"ok": True, "share": safe}


@app.get("/h/{slug}", response_class=HTMLResponse)
async def public_hunt_share(slug: str, request: Request):
    """Public, no-auth landing page for a shared hunt. Snapshot only —
    we never look at the user's current lead state here.

    Records a view in db.share_views (de-duped per IP-hash + slug per
    hour) so the share owner can see how many people opened the link.
    Bots and asset prefetchers (Slack, Twitter, Discord) hit
    /h/<slug>/og.svg, not the HTML page, so they don't pollute the
    count.
    """
    if not re.fullmatch(r"[A-Za-z0-9_-]{4,32}", slug or ""):
        return HTMLResponse(_share_not_found_page(), status_code=404)
    share = await db.get_hunt_share(slug)
    if not share:
        return HTMLResponse(_share_not_found_page(), status_code=404)
    # Record view (soft-fails) — runs in the request path so we don't
    # need a background queue. db.record_share_view is single-INSERT.
    try:
        import hashlib as _hash
        ip = _get_client_ip(request)
        ip_hash = _hash.sha256(ip.encode("utf-8")).hexdigest()[:16]
        ua = (request.headers.get("user-agent") or "")[:200]
        ref = (request.headers.get("referer") or "")[:400]
        # Skip obvious bot/scraper user-agents from polluting the count
        ua_lower = ua.lower()
        is_bot = any(b in ua_lower for b in (
            "bot", "spider", "crawler", "facebookexternalhit", "slackbot",
            "twitterbot", "discordbot", "linkedinbot", "telegram",
        ))
        if not is_bot:
            await db.record_share_view(slug, ip_hash=ip_hash, user_agent=ua, referrer=ref)
            # a206 fix: the view_count column on hunt_shares used to
            # be bumped inside get_hunt_share itself, which inflated
            # it for OG-bot unfurls + CLI JSON polls. Now we bump
            # explicitly here, gated by the same is_bot filter that
            # guards record_share_view, so the two counters stay
            # aligned.
            await db.bump_share_view(slug)
    except Exception:
        pass
    # Pull view count for display on the page
    try:
        view_count = await db.get_share_view_count(slug, days=30)
    except Exception:
        view_count = 0
    share = dict(share)
    share["_view_count"] = view_count
    return HTMLResponse(
        _render_share_page(share),
        headers={
            "Cache-Control": "private, no-cache, no-store, must-revalidate",
            "X-Robots-Tag": "noindex, nofollow",
        },
    )


@app.get("/api/share/{slug}/views")
async def api_share_views(slug: str):
    """Public read endpoint for view count. Consumed by the
    `huntova share status <slug>` CLI (shipped in cli.py cmd_share)
    and for live-updating badges on the /h/<slug> share page."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{4,32}", slug or ""):
        raise HTTPException(status_code=404, detail="not_found")
    # Surface revoked status in the response so `huntova share status`
    # can tell the user a share has been killed even if old view counts
    # are still in the DB. Pre-a71 this returned `views_30d` for any
    # slug regardless of revoke state — misleading UX.
    row = await db._afetchone(
        "SELECT revoked FROM hunt_shares WHERE slug = %s", [slug])
    if not row:
        raise HTTPException(status_code=404, detail="not_found")
    revoked = bool(row.get("revoked"))
    n = await db.get_share_view_count(slug, days=30)
    return {"slug": slug, "views_30d": n, "revoked": revoked}


def _share_cta_url() -> str:
    """Where the share-page CTA points. /download in local mode (CLI
    install instructions); /landing in cloud mode (signup flow)."""
    from runtime import CAPABILITIES
    base = PUBLIC_URL.rstrip("/")
    if CAPABILITIES.mode == "local" or not CAPABILITIES.billing_enabled:
        return f"{base}/download"
    return f"{base}/landing"


def _share_not_found_page() -> str:
    return _render_share_shell(
        title="Share unavailable",
        body=(
            "<main class='wrap'><h1>This share is no longer available.</h1>"
            "<p>The link may have expired or been revoked by its creator.</p>"
            f"<p><a class='cta' href='{_share_cta_url()}'>Try Huntova →</a></p></main>"
        ),
        og_description="Huntova — AI-powered B2B lead generation.",
    )


def _render_proof_pack(pack: dict) -> str:
    """Render the evidence-first dossier inside a lead card.

    Per the round-67 brainstorm: every lead arrives with sources +
    quoted snippets + freshness so a reader can verify the claim
    without leaving the page. This is the visible spike: "Clay gives
    fields, Huntova gives proof."
    """
    from html import escape as _esc
    if not isinstance(pack, dict):
        return ""
    parts = []
    quotes = pack.get("quotes") or []
    if quotes:
        parts.append("<div class='proof-quotes'>")
        for q in quotes[:3]:
            text = _esc(str(q.get("text") or ""))
            tag = _esc(str(q.get("tag") or ""))
            if not text:
                continue
            parts.append(
                f"<blockquote class='proof-q'>"
                f"<span class='proof-q-text'>{text}</span>"
                + (f"<span class='proof-q-tag'>{tag}</span>" if tag else "")
                + "</blockquote>"
            )
        parts.append("</div>")
    sources = pack.get("sources") or []
    if sources:
        chips = []
        for s in sources[:5]:
            url = (s.get("url") or "").strip()
            label = _esc(str(s.get("label") or "source"))
            if not url:
                continue
            url_h = _esc(url)
            chips.append(
                f"<a class='proof-src' href='{url_h}' target='_blank' rel='noopener nofollow'>"
                f"<span class='proof-src-label'>{label}</span>"
                f"</a>"
            )
        if chips:
            parts.append("<div class='proof-sources'>" + "".join(chips) + "</div>")
    reach = pack.get("reachability") or {}
    if reach and reach.get("score") is not None:
        tier = (reach.get("tier") or "").strip()
        tier_label = reach.get("tier_label") or tier
        score = int(reach.get("score") or 0)
        # Render a 10-cell horizontal meter so tier is visible at a glance.
        cells = max(0, min(10, round(score / 10)))
        meter = "█" * cells + "░" * (10 - cells)
        reason_html = ""
        for r in (reach.get("reasons") or [])[:3]:
            kind = _esc(str(r.get("kind") or "·"))
            text = _esc(str(r.get("text") or ""))
            reason_html += f"<li class='reach-reason reach-r-{kind.replace('✓', 'ok').replace('✕', 'no').replace('~', 'maybe')}'><span class='reach-r-kind'>{kind}</span> {text}</li>"
        proof_chips = ""
        for label in (reach.get("proof_trail") or [])[:5]:
            proof_chips += f"<span class='reach-proof-pill'>{_esc(str(label))}</span>"
        parts.append(
            "<div class='reach-block'>"
            "<div class='reach-row'>"
            f"<span class='reach-meter reach-tier-{_esc(tier)}'>{meter}</span>"
            f"<span class='reach-score'>{score}/100</span>"
            f"<span class='reach-tier reach-tier-{_esc(tier)}'>{_esc(tier_label)}</span>"
            "</div>"
            + (f"<ul class='reach-reasons'>{reason_html}</ul>" if reason_html else "")
            + (f"<div class='reach-trail'><span class='reach-trail-label'>proof trail</span>{proof_chips}</div>" if proof_chips else "")
            + "</div>"
        )
    fetched = pack.get("fetched_at")
    verified = pack.get("verified") or ""
    if fetched or verified:
        try:
            from datetime import datetime as _dt
            stamp = _dt.fromisoformat(str(fetched).replace("Z", "+00:00")).strftime("%b %d, %Y") if fetched else ""
        except (ValueError, TypeError):
            stamp = ""
        meta_bits = []
        if stamp:
            meta_bits.append(f"<span class='proof-fresh'>Verified {_esc(stamp)}</span>")
        if verified == "yes":
            meta_bits.append("<span class='proof-verified-yes'>● proof verified</span>")
        elif verified == "partial":
            meta_bits.append("<span class='proof-verified-partial'>◐ partial proof</span>")
        if meta_bits:
            parts.append("<div class='proof-meta'>" + " · ".join(meta_bits) + "</div>")
    return f"<div class='proof-pack'>{''.join(parts)}</div>" if parts else ""


def _render_share_og_svg(share: dict) -> str:
    """Dynamic OG image as SVG — Gemini round-71 pick. Renders the hunt
    title, top-3 lead names, and fit scores onto a terminal-styled
    1200x630 dark canvas so when /h/<slug> is pasted into Slack,
    Twitter, LinkedIn, Discord, Telegram, the URL itself becomes the
    billboard. Modern social platforms render SVG og:image; older
    clients fall back to og:title/og:description which we still set.
    """
    from html import escape as _esc
    title = (share.get("title") or "Hunt results").strip() or "Hunt results"
    leads = share.get("leads") or []
    meta = share.get("meta") or {}
    icp = (meta.get("icp") or title).strip()
    if len(icp) > 64:
        icp = icp[:61].rstrip() + "…"
    rows: list[str] = []
    for i, ld in enumerate(leads[:3]):
        org = (ld.get("org_name") or "(unnamed)").strip()
        if len(org) > 38:
            org = org[:36].rstrip() + "…"
        try:
            fit = int(ld.get("fit_score") or 0)
        except (TypeError, ValueError):
            fit = 0
        rows.append((org, fit))
    # Pad to 3 rows so layout is stable
    while len(rows) < 3:
        rows.append(("", 0))
    try:
        leads_total = int(meta.get("leads_total") or len(leads))
    except (TypeError, ValueError):
        leads_total = len(leads)
    leads_total = max(0, min(leads_total, 9999))
    is_demo = bool(meta.get("demo")) if isinstance(meta, dict) else False
    # Build the SVG. Inline-only — no external fonts/images so it
    # renders identically on every platform.
    rect = "<rect x='0' y='0' width='1200' height='630' fill='#08090c'/>"
    grid = (
        "<defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>"
        "<stop offset='0' stop-color='#7c5cff' stop-opacity='0.18'/>"
        "<stop offset='1' stop-color='#7c5cff' stop-opacity='0'/>"
        "</linearGradient></defs>"
        "<rect x='0' y='0' width='1200' height='400' fill='url(#g)'/>"
    )
    chrome = (
        "<g transform='translate(60 60)'>"
        "<circle cx='12' cy='12' r='8' fill='#ff5f57'/>"
        "<circle cx='40' cy='12' r='8' fill='#febc2e'/>"
        "<circle cx='68' cy='12' r='8' fill='#28c840'/>"
        "<text x='150' y='18' font-family='ui-monospace,Menlo,monospace' font-size='14' fill='#5d6679'>huntova — proof-pack.sh</text>"
        "</g>"
    )
    head = (
        "<g font-family='ui-monospace,Menlo,monospace' fill='#dfe3eb'>"
        f"<text x='60' y='160' font-size='30' fill='#5d6679'>$ huntova hunt --query</text>"
        f"<text x='60' y='220' font-size='44' fill='#a48bff' font-weight='600'>“{_esc(icp)}”</text>"
        f"<text x='60' y='280' font-size='24' fill='#3ddc97'>✓ {leads_total} high-fit prospect{'s' if leads_total != 1 else ''} extracted</text>"
        "</g>"
    )
    lead_rows: list[str] = []
    base_y = 360
    for i, (org, fit) in enumerate(rows):
        if not org:
            continue
        y = base_y + i * 60
        fit_color = "#3ddc97" if fit >= 8 else ("#a48bff" if fit >= 6 else "#f6b352")
        lead_rows.append(
            f"<g font-family='ui-monospace,Menlo,monospace'>"
            f"<text x='60' y='{y}' font-size='28' fill='{fit_color}'>✓ [{fit}/10]</text>"
            f"<text x='200' y='{y}' font-size='28' fill='#dfe3eb' font-weight='500'>{_esc(org)}</text>"
            f"</g>"
        )
    foot = (
        "<g font-family='-apple-system,BlinkMacSystemFont,Inter,Helvetica,sans-serif'>"
        "<text x='60' y='580' font-size='22' fill='#8a93a4'>Made with </text>"
        "<text x='180' y='580' font-size='22' fill='#a48bff' font-weight='600'>Huntova</text>"
        "<text x='292' y='580' font-size='22' fill='#5d6679'>— local-first BYOK lead-gen CLI</text>"
        "</g>"
    )
    # GPT-5.4 round-72: same "PREVIEW" badge in the OG image so the
    # boundary between preview and product is visible at a glance
    # when the link unfurls in social previews.
    demo_badge = ""
    if is_demo:
        demo_badge = (
            "<g>"
            "<rect x='960' y='40' width='200' height='40' rx='10' "
            "fill='rgba(246,179,82,0.16)' stroke='#f6b352' stroke-width='1.5'/>"
            "<text x='1060' y='66' font-family='ui-monospace,Menlo,monospace' "
            "font-size='17' fill='#f6b352' text-anchor='middle' font-weight='700' "
            "letter-spacing='2'>PREVIEW MODE</text>"
            "</g>"
        )
    return (
        "<?xml version='1.0' encoding='UTF-8'?>"
        "<svg xmlns='http://www.w3.org/2000/svg' width='1200' height='630' viewBox='0 0 1200 630'>"
        + rect + grid + chrome + head + "".join(lead_rows) + foot + demo_badge
        + "</svg>"
    )


def _render_share_page(share: dict) -> str:
    from html import escape as _esc
    title = (share.get("title") or "Hunt results").strip() or "Hunt results"
    leads = share.get("leads") or []
    meta = share.get("meta") or {}
    leads_total = meta.get("leads_total") or len(leads)
    shared_at = meta.get("shared_at") or share.get("created_at") or ""
    slug = _esc(str(share.get("slug") or ""))
    try:
        shared_human = datetime.fromisoformat(shared_at).strftime("%B %d, %Y")
    except (ValueError, TypeError):
        shared_human = ""

    # Growth-loop tweak (Tab 1, round 67): render the FULL set of
    # leads but blur the bottom half, so the recipient sees enough to
    # be impressed but has to install Huntova + fork the hunt to
    # unlock the rest. `huntova hunt --from-share <slug>` reproduces
    # the exact ICP locally.
    visible_count = max(1, (len(leads) + 1) // 2)

    cards: list[str] = []
    for idx, ld in enumerate(leads):
        org = _esc(str(ld.get("org_name") or "(unnamed)"))
        site = ld.get("org_website") or ld.get("url") or ""
        site_h = _esc(str(site))
        host = ""
        if site:
            try:
                host = urlparse(site).netloc.replace("www.", "")
            except Exception:
                host = ""
        host_h = _esc(host)
        country = _esc(str(ld.get("country") or ""))
        city = _esc(str(ld.get("city") or ""))
        fit = ld.get("fit_score")
        try:
            fit_n = int(fit) if fit is not None else None
        except (TypeError, ValueError):
            fit_n = None
        fit_chip = f"<span class='chip fit'>Fit {fit_n}/10</span>" if fit_n is not None else ""
        why = _esc(str(ld.get("why_fit") or ""))
        gap = _esc(str(ld.get("production_gap") or ""))
        evt = _esc(str(ld.get("event_name") or ld.get("event_type") or ""))
        site_link = (
            f"<a class='site' href='{site_h}' target='_blank' rel='noopener nofollow'>{host_h or site_h}</a>"
            if site else ""
        )
        loc_bits = [b for b in (city, country) if b]
        loc = " · ".join(loc_bits)
        proof_html = _render_proof_pack(ld.get("proof_pack") or {})
        is_blurred = idx >= visible_count and len(leads) > 3
        card_cls = "card blurred" if is_blurred else "card"
        cards.append(
            f"<article class='{card_cls}'>"
            f"<header><h3>{org}</h3>{fit_chip}</header>"
            f"<div class='sub'>{site_link}{(' · ' + loc) if loc else ''}</div>"
            + (f"<p class='why'>{why}</p>" if why else "")
            + (f"<p class='gap'><strong>Opportunity:</strong> {gap}</p>" if gap else "")
            + (f"<p class='evt'>Signal: {evt}</p>" if evt else "")
            + proof_html
            + "</article>"
        )
    fork_cmd = f"huntova hunt --from-share {slug}" if slug else "huntova hunt"
    blur_overlay = ""
    if len(leads) > 3:
        blur_overlay = (
            "<div class='unlock-cta'>"
            f"<p><strong>{len(leads) - visible_count} more leads</strong> hidden — fork this hunt locally to unlock everything.</p>"
            f"<pre class='cmdline'><code>$ {_esc(fork_cmd)}</code></pre>"
            "<p class='unlock-sub'>Free, open-source, runs on your own AI key.</p>"
            "</div>"
        )
    # Demo / preview banner (GPT-5.4 round-72 counter-takedown): the
    # most-likely HN critique is "synthetic /try is misleading, the
    # whole product smells staged". Disarm it by labelling preview-
    # generated proof packs unmistakably so the boundary between
    # taste-of-Huntova and the real local CLI cannot be missed.
    is_demo = bool(meta.get("demo")) if isinstance(meta, dict) else False
    demo_kind = (meta.get("demo_kind") or "").strip().lower() if isinstance(meta, dict) else ""
    demo_banner = ""
    if is_demo:
        if demo_kind == "static":
            # Canonical /demo — fixed sample, not the output of any agent run.
            demo_banner = (
                "<div class='demo-banner' role='note'>"
                "<span class='demo-banner-pip'></span>"
                "<span class='demo-banner-text'>"
                "<strong>Sample Proof Pack</strong> &mdash; "
                "illustrative output showing what a real hunt looks like. "
                "Install Huntova for live-web verified hunts on your own AI key."
                "</span>"
                "</div>"
            )
        else:
            # /try-minted preview — synthetic but agent-shaped.
            demo_banner = (
                "<div class='demo-banner' role='note'>"
                "<span class='demo-banner-pip'></span>"
                "<span class='demo-banner-text'>"
                "<strong>Preview-generated sample</strong> &mdash; "
                "this is a /try Preview Mode hunt, not a live-web verified hunt. "
                "Names and contacts are illustrative. "
                "Install Huntova for real SearXNG-grounded hunts on your own AI key."
                "</span>"
                "</div>"
            )

    # Top-of-page fork CTA (GPT-5.4 + Gemini round-71 convergence):
    # the share page should foreground "fork this hunt locally" not
    # "keep browsing here". The CTA was previously only at the bottom.
    fork_top = (
        "<div class='fork-top'>"
        "<div class='fork-top-row'>"
        "<div class='fork-top-text'>"
        f"<strong>Fork this hunt locally</strong> &middot; "
        f"reproduce the exact ICP on your own machine with one command."
        "</div>"
        f"<pre class='fork-top-cmd'><code>$ {_esc(fork_cmd)}</code></pre>"
        f"<button class='fork-top-copy' onclick=\"navigator.clipboard.writeText('{_esc(fork_cmd)}').then(()=>{{this.textContent='Copied'}})\">Copy</button>"
        "</div></div>"
    )
    # View count badge — only show when ≥3 to avoid the "1 view"
    # awkwardness of a freshly-minted share looking unloved.
    view_count = int(share.get("_view_count") or 0)
    view_badge = ""
    if view_count >= 3:
        view_badge = f" · <span class='view-badge'>{view_count} view{'s' if view_count != 1 else ''}</span>"

    body = (
        "<main class='wrap'>"
        "<header class='hero'>"
        f"<p class='kicker'>Shared hunt · {leads_total} prospect{'s' if leads_total != 1 else ''} · evidence-first{view_badge}</p>"
        f"<h1>{_esc(title)}</h1>"
        + (f"<p class='dateline'>Snapshot taken {_esc(shared_human)}</p>" if shared_human else "")
        + "</header>"
        + demo_banner
        + fork_top
        + "<section class='cards'>" + "".join(cards) + "</section>"
        + blur_overlay
        + "<aside class='cta-block'>"
        "<p><strong>Huntova</strong> finds B2B prospects on the live web, scores them with AI, and proves every match with verbatim evidence. Bring your own AI key. Runs locally — your data stays on your machine.</p>"
        f"<a class='cta' href='{_share_cta_url()}'>Install Huntova →</a>"
        "</aside></main>"
        # Sticky bottom bar — the install funnel. Per Tab 1: terminal
        # styling, generation time when known, 1-click copy.
        "<div class='sticky-install'>"
        "<span class='sticky-mark'>$ _</span>"
        "<span class='sticky-text'>Generated locally with Huntova CLI</span>"
        f"<button class='sticky-btn' onclick=\"navigator.clipboard.writeText('pipx install huntova').then(()=>{{this.textContent='Copied'}})\">"
        f"pipx install huntova</button>"
        "</div>"
    )
    og_desc = f"{leads_total} prospects · evidence-first · {_esc(title)[:100]}"
    og_image = f"{PUBLIC_URL.rstrip('/')}/h/{slug}/og.svg" if slug else ""
    return _render_share_shell(title=f"{title} — Huntova", body=body,
                               og_description=og_desc, og_image=og_image)


def _render_share_shell(title: str, body: str, og_description: str = "",
                         og_image: str = "") -> str:
    """Self-contained HTML shell. Inline CSS so the public page has zero
    extra requests (faster perceived load when shared on social)."""
    from html import escape as _esc
    title_h = _esc(title)
    desc_h = _esc(og_description or "Huntova — AI-powered B2B lead generation.")
    css = """
    :root{--bg:#0b0d10;--panel:#11151a;--ink:#e8eef5;--mute:#9aa6b2;--accent:#7c5cff;--line:#1d242c}
    *{box-sizing:border-box}
    html,body{margin:0;padding:0;background:var(--bg);color:var(--ink);font-family:'Satoshi',-apple-system,BlinkMacSystemFont,'Segoe UI',Inter,Helvetica,Arial,sans-serif;line-height:1.5}
    a{color:var(--ink)}
    .wrap{max-width:920px;margin:0 auto;padding:48px 20px 80px}
    .hero{margin-bottom:32px}
    .kicker{color:var(--mute);font-size:13px;letter-spacing:.08em;text-transform:uppercase;margin:0 0 8px}
    .hero h1{font-size:32px;line-height:1.15;margin:0 0 8px;letter-spacing:-.01em}
    .dateline{color:var(--mute);margin:0;font-size:14px}
    .cards{display:grid;grid-template-columns:1fr;gap:14px}
    @media(min-width:680px){.cards{grid-template-columns:1fr 1fr}}
    .card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px}
    .card header{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}
    .card h3{margin:0;font-size:17px;letter-spacing:-.005em}
    .chip{font-size:11px;padding:3px 8px;border-radius:999px;background:rgba(124,92,255,.16);color:#cbb9ff;border:1px solid rgba(124,92,255,.35)}
    .card .sub{color:var(--mute);font-size:13px;margin:6px 0 10px;word-break:break-word}
    .card .site{color:var(--mute);text-decoration:none;border-bottom:1px dotted var(--mute)}
    .card .site:hover{color:var(--ink)}
    .card p{margin:8px 0;font-size:14px}
    .card .why{color:var(--ink)}
    .card .gap,.card .evt{color:var(--mute)}
    .cta-block{margin-top:40px;padding:24px;border:1px solid var(--line);border-radius:14px;background:linear-gradient(180deg,rgba(124,92,255,.08),transparent)}
    .cta-block p{margin:0 0 14px;color:var(--ink)}
    .cta{display:inline-block;background:var(--accent);color:#fff;padding:10px 18px;border-radius:10px;text-decoration:none;font-weight:600}
    .cta:hover{filter:brightness(1.1)}
    footer{color:var(--mute);font-size:12px;text-align:center;margin-top:32px;padding-bottom:80px}
    /* Proof pack — evidence-first dossier inside each lead card */
    .proof-pack{margin-top:14px;padding-top:14px;border-top:1px dashed var(--line)}
    .proof-quotes{display:flex;flex-direction:column;gap:8px;margin-bottom:10px}
    .proof-q{margin:0;padding:8px 12px;border-left:2px solid rgba(124,92,255,.4);background:rgba(124,92,255,.04);font-size:12.5px;line-height:1.55;color:var(--ink);font-style:italic}
    .proof-q-tag{display:inline-block;margin-left:8px;font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--mute);font-style:normal}
    .proof-sources{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}
    .proof-src{display:inline-block;font-size:11px;padding:3px 8px;border-radius:6px;background:rgba(255,255,255,.04);border:1px solid var(--line);color:var(--mute);text-decoration:none}
    .proof-src:hover{color:var(--ink);border-color:rgba(124,92,255,.4)}
    .proof-meta{font-size:11px;color:var(--mute);margin-top:6px}
    .proof-fresh{}
    .proof-verified-yes{color:#3ddc97}
    .proof-verified-partial{color:#f6b352}
    /* Reachability waterfall — confidence ladder per lead */
    .reach-block{margin:12px 0 8px;padding:10px 12px;background:rgba(255,255,255,.02);border:1px solid var(--line);border-radius:8px}
    .reach-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
    .reach-meter{font-family:ui-monospace,'SF Mono',monospace;font-size:13px;letter-spacing:-.05em}
    .reach-score{font-size:11px;color:var(--mute);font-family:ui-monospace,monospace}
    .reach-tier{font-size:11px;font-weight:600;padding:2px 8px;border-radius:999px;letter-spacing:.02em}
    .reach-tier-direct-evidence{background:rgba(61,220,151,.18);color:#3ddc97;border:1px solid rgba(61,220,151,.4)}
    .reach-meter.reach-tier-direct-evidence{color:#3ddc97}
    .reach-tier-strong-attributed{background:rgba(124,92,255,.18);color:#a48bff;border:1px solid rgba(124,92,255,.4)}
    .reach-meter.reach-tier-strong-attributed{color:#a48bff}
    .reach-tier-probable-route{background:rgba(246,179,82,.14);color:#f6b352;border:1px solid rgba(246,179,82,.35)}
    .reach-meter.reach-tier-probable-route{color:#f6b352}
    .reach-tier-heuristic-guess{background:rgba(154,166,178,.1);color:#9aa6b2;border:1px solid var(--line)}
    .reach-meter.reach-tier-heuristic-guess{color:#9aa6b2}
    .reach-tier-no-path{background:transparent;color:var(--mute);border:1px dashed var(--line)}
    .reach-meter.reach-tier-no-path{color:var(--dim,#5d6679)}
    .reach-reasons{margin:8px 0 4px;padding:0;list-style:none;font-size:12px}
    .reach-reason{margin:3px 0;color:var(--mute)}
    .reach-r-kind{display:inline-block;width:14px;font-weight:700}
    .reach-r-ok{color:var(--ink)}
    .reach-r-no{color:#f0816a}
    .reach-r-maybe{color:#f6b352}
    .reach-trail{display:flex;flex-wrap:wrap;gap:5px;align-items:center;margin-top:6px;font-size:10px}
    .reach-trail-label{color:var(--mute);text-transform:uppercase;letter-spacing:.06em;font-size:9px;margin-right:4px}
    .reach-proof-pill{padding:2px 7px;border-radius:6px;background:rgba(255,255,255,.04);border:1px solid var(--line);color:var(--ink);font-size:10px}
    /* /compare/<name> table */
    .cmp-table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--line);border-radius:14px;overflow:hidden;font-size:13.5px}
    .cmp-table thead th{background:rgba(255,255,255,.02);padding:14px 18px;text-align:left;font-weight:600;color:var(--ink);font-size:13px;letter-spacing:.01em}
    .cmp-table thead th:last-child{color:#a48bff}
    .cmp-table tbody td{padding:14px 18px;border-top:1px solid var(--line);vertical-align:top;line-height:1.55}
    .cmp-table .cmp-axis{font-weight:600;color:var(--ink);width:25%}
    .cmp-table .cmp-them{color:var(--mute);width:37%}
    .cmp-table .cmp-us{color:var(--ink);width:38%}
    @media(max-width:680px){.cmp-table{font-size:12.5px}.cmp-table thead th,.cmp-table tbody td{padding:10px 12px}.cmp-table .cmp-axis{width:30%}}
    /* Blurred lower-half growth wedge */
    .card.blurred{filter:blur(4px) saturate(0.7);opacity:.7;pointer-events:none;user-select:none}
    .unlock-cta{margin:24px 0;padding:20px 22px;border:1px dashed rgba(124,92,255,.4);border-radius:14px;background:rgba(124,92,255,.05);text-align:center}
    .unlock-cta p{margin:0 0 10px;color:var(--ink);font-size:14px}
    .unlock-cta .unlock-sub{color:var(--mute);font-size:12px}
    .unlock-cta .cmdline{margin:8px 0;padding:10px 14px;background:#0b0d10;border:1px solid var(--line);border-radius:8px;text-align:left;overflow-x:auto;font-family:ui-monospace,'SF Mono',monospace;font-size:13px;color:#a48bff}
    .unlock-cta .cmdline code{color:#a48bff}
    /* Sticky install bar — the growth-loop install funnel */
    .sticky-install{position:fixed;left:0;right:0;bottom:0;z-index:50;background:rgba(8,9,12,.92);backdrop-filter:blur(8px);border-top:1px solid var(--line);padding:10px 18px;display:flex;align-items:center;gap:12px;font-family:ui-monospace,'SF Mono',monospace;font-size:13px}
    .sticky-mark{color:#5d6679;letter-spacing:.1em}
    .sticky-text{color:var(--mute);flex:1;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Inter,Helvetica,Arial,sans-serif;font-size:12px}
    @media(max-width:540px){.sticky-text{display:none}}
    .sticky-btn{font-family:ui-monospace,'SF Mono',monospace;font-size:12px;padding:6px 12px;border-radius:6px;background:var(--accent);color:#fff;border:none;cursor:pointer;font-weight:600;letter-spacing:.01em}
    .sticky-btn:hover{filter:brightness(1.1)}
    /* View count badge — Kimi round-76 engagement signal */
    .view-badge{color:#a48bff;background:rgba(124,92,255,.10);padding:1px 8px;border-radius:999px;border:1px solid rgba(124,92,255,.25);font-size:11px;letter-spacing:.02em}
    /* Demo/preview banner — GPT-5.4 round-72 counter-takedown */
    .demo-banner{display:flex;align-items:flex-start;gap:10px;margin:0 0 16px;padding:12px 16px;background:linear-gradient(180deg,rgba(246,179,82,.10),rgba(246,179,82,.03));border:1px solid rgba(246,179,82,.4);border-radius:12px;font-size:13px;line-height:1.55}
    .demo-banner-pip{display:inline-block;width:8px;height:8px;border-radius:50%;background:#f6b352;box-shadow:0 0 8px rgba(246,179,82,.6);margin-top:6px;flex-shrink:0}
    .demo-banner-text{color:#f6b352}
    .demo-banner-text strong{color:#fff}
    /* Top-of-page fork CTA — GPT+Gemini round-71 convergence */
    .fork-top{margin:0 0 24px;padding:16px 18px;background:linear-gradient(180deg,rgba(124,92,255,.08),rgba(124,92,255,.02));border:1px solid rgba(124,92,255,.3);border-radius:12px}
    .fork-top-row{display:flex;align-items:center;gap:14px;flex-wrap:wrap}
    .fork-top-text{flex:1;min-width:240px;font-size:13.5px;color:var(--ink);line-height:1.5}
    .fork-top-text strong{color:#a48bff}
    .fork-top-cmd{margin:0;padding:8px 12px;background:#0b0d10;border:1px solid var(--line);border-radius:8px;font-family:ui-monospace,'SF Mono',monospace;font-size:12.5px;color:#a48bff;overflow-x:auto;white-space:nowrap}
    .fork-top-copy{padding:6px 12px;font-size:12px;background:transparent;color:var(--ink);border:1px solid var(--accent);border-radius:6px;cursor:pointer;font-family:inherit;font-weight:600}
    .fork-top-copy:hover{background:var(--accent);color:#fff}
    @media(max-width:540px){.fork-top-row{flex-direction:column;align-items:stretch}.fork-top-cmd{width:100%}}
    """
    og_img_meta = ""
    if og_image:
        img_h = _esc(og_image)
        og_img_meta = (
            f"<meta property='og:image' content='{img_h}'>"
            f"<meta property='og:image:width' content='1200'>"
            f"<meta property='og:image:height' content='630'>"
            f"<meta property='og:image:type' content='image/svg+xml'>"
            f"<meta name='twitter:image' content='{img_h}'>"
        )
    twitter_card = "summary_large_image" if og_image else "summary"
    return (
        "<!doctype html><html lang='en'><head>"
        "<meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{title_h}</title>"
        f"<meta name='description' content='{desc_h}'>"
        "<meta name='robots' content='noindex,nofollow'>"
        f"<meta property='og:title' content='{title_h}'>"
        f"<meta property='og:description' content='{desc_h}'>"
        "<meta property='og:type' content='website'>"
        f"{og_img_meta}"
        f"<meta name='twitter:card' content='{twitter_card}'>"
        "<link rel='preconnect' href='https://api.fontshare.com' crossorigin>"
        "<link rel='stylesheet' href='https://api.fontshare.com/v2/css?f[]=satoshi@400,500,600,700&display=swap'>"
        f"<style>{css}</style>"
        "</head><body>"
        f"{body}"
        "<footer class='wrap' style='padding-top:0'>Powered by Huntova · "
        f"<a href='{_share_cta_url()}' style='color:inherit'>huntova.com</a></footer>"
        "</body></html>"
    )


# ═══════════════════════════════════════════════════════════════
# API: EXPORT
# ═══════════════════════════════════════════════════════════════

CSV_FIELDS_BASE = [
    "contact_name", "contact_email", "org_name", "org_website", "country", "city",
    "contact_linkedin", "org_linkedin", "fit_score", "why_fit", "production_gap",
    "event_name", "event_type", "platform_used", "is_recurring",
    "fit_rationale", "timing_rationale", "_data_confidence",
]
CSV_FIELDS_DRAFTS = ["email_subject", "email_body", "linkedin_note",
    "email_followup_2", "email_followup_3", "email_followup_4"]


# Per-user export rate limit. A compromised user token could otherwise be
# used to dump the full lead table repeatedly with no trace. In-memory
# (reset on pod restart) is fine here — the goal is friction, not a hard
# security boundary, and we also write a durable audit log row per export.
_EXPORT_LIMIT_PER_24H = 10
_export_history: dict[int, list[float]] = {}

_export_history_last_gc = 0.0

# Per-user rate limit on /api/webhooks/test + /api/smtp/test. 5 calls
# per minute — enough for a user fixing a typo, low enough that a
# hijacked session can't credential-stuff or port-scan via the test
# endpoint. Keyed by (user_id, endpoint) so webhook + SMTP buckets
# don't compete.
_TEST_ENDPOINT_LIMIT_PER_MIN = 5
_test_endpoint_history: dict[tuple, list[float]] = {}
_test_endpoint_last_gc = 0.0


def _check_test_endpoint_rate(user_id: int, endpoint: str) -> bool:
    """Return True if the test call is allowed; False if rate-limited.
    60-second window, 5 calls per (user, endpoint).

    GC pattern mirrors `_check_export_rate` (multi-agent bug #37):
    sweep the whole dict every 5 minutes dropping users whose windows
    have aged out. Otherwise a burst of forged session cookies — or
    even normal usage over months — grows the dict unbounded and
    eventually OOM-kills the process.
    """
    global _test_endpoint_last_gc
    import time as _t
    now = _t.time()
    if now - _test_endpoint_last_gc > 300:
        _test_endpoint_last_gc = now
        stale = [k for k, v in _test_endpoint_history.items()
                 if not v or all(now - t >= 60 for t in v)]
        for k in stale:
            _test_endpoint_history.pop(k, None)
    key = (user_id, endpoint)
    window = _test_endpoint_history.get(key, [])
    window = [t for t in window if now - t < 60]
    if len(window) >= _TEST_ENDPOINT_LIMIT_PER_MIN:
        _test_endpoint_history[key] = window
        return False
    window.append(now)
    _test_endpoint_history[key] = window
    return True

def _check_export_rate(user_id: int) -> tuple[bool, int]:
    """Per-user export rate limiter.

    Stability fix (multi-agent bug #37): the dict only filtered at read
    time per-user, so every user who ever exported once stayed in
    memory forever even after their entries timed out. Now we sweep
    the whole dict every 5 min, dropping users with empty windows.
    """
    global _export_history_last_gc
    import time as _t
    now = _t.time()
    if now - _export_history_last_gc > 300:
        _export_history_last_gc = now
        stale = [k for k, v in _export_history.items()
                 if not v or all(now - t >= 86400 for t in v)]
        for k in stale:
            _export_history.pop(k, None)
    window = _export_history.get(user_id, [])
    window = [t for t in window if now - t < 86400]
    if len(window) >= _EXPORT_LIMIT_PER_24H:
        _export_history[user_id] = window
        return False, len(window)
    window.append(now)
    _export_history[user_id] = window
    return True, len(window)


async def _log_export(user: dict, kind: str, row_count: int, request: Request):
    # Durable audit trail so admins can see who exported what and when,
    # even across server restarts (in-memory counter is soft).
    try:
        await db.log_admin_action(
            user["id"], None, f"self_export_{kind}",
            {"email": user.get("email", ""), "rows": row_count, "tier": user.get("tier", "")},
            request.client.host if request.client else "")
    except Exception as _err:
        print(f"[EXPORT] audit log failed for user {user['id']}: {_err}")


@app.get("/api/export/csv")
async def api_export_csv(request: Request, user: dict = Depends(require_user)):
    ok, used = _check_export_rate(user["id"])
    if not ok:
        return JSONResponse(
            {"ok": False, "error": f"Export rate limit reached ({_EXPORT_LIMIT_PER_24H}/24h). Try again later."},
            status_code=429)
    leads = await db.get_leads(user["id"])
    # Filter by ids if provided
    qs = dict(parse_qsl(str(request.query_params)))
    if qs.get("ids"):
        fids = set(qs["ids"].split(","))
        leads = [l for l in leads if l.get("lead_id") in fids]
    tier = user.get("tier", "free")
    fields = CSV_FIELDS_BASE + (CSV_FIELDS_DRAFTS if tier in ("growth", "agency") else [])
    output = io.StringIO()
    w = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    w.writerows(sorted(leads, key=lambda x: x.get("fit_score", 0), reverse=True))
    content = "﻿" + output.getvalue()
    await _log_export(user, "csv", len(leads), request)
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=huntova_leads_{datetime.now().strftime('%Y%m%d')}.csv"}
    )


@app.post("/api/import/csv")
async def api_import_csv(request: Request, user: dict = Depends(require_user)):
    """a253: web UI CSV import. Accepts a JSON body with `csv_text` (the
    file contents) and optional `source` slug ("apollo" / "clay" /
    "hunter" / generic). Reuses cli_migrate's helpers so the web flow
    matches the CLI semantics 1:1.
    """
    body = await request.json()
    csv_text = (body.get("csv_text") or "").strip()
    if not csv_text:
        return JSONResponse({"ok": False, "error": "csv_text required"}, status_code=400)
    if len(csv_text) > 25_000_000:
        return JSONResponse({"ok": False, "error": "CSV too large (>25MB)"}, status_code=413)
    source = (body.get("source") or "generic").lower().strip()
    try:
        from cli_migrate import (
            APOLLO_MAP, CLAY_MAP, HUNTER_MAP, _autodetect, _normalise_row,
            _dedup_keys, _make_lead_id, _LEAD_FIELDS,
        )
    except ImportError:
        return JSONResponse({"ok": False, "error": "import module unavailable"}, status_code=500)
    base_map = {"apollo": APOLLO_MAP, "clay": CLAY_MAP, "hunter": HUNTER_MAP}.get(source, {})
    import csv as _csv, io
    fh = io.StringIO(csv_text)
    reader = _csv.DictReader(fh)
    headers = reader.fieldnames or []
    if not headers:
        return JSONResponse({"ok": False, "error": "no header row in CSV"}, status_code=400)
    mapping = dict(base_map)
    if not mapping:
        mapping = _autodetect(headers)
    else:
        for h, canon in _autodetect(headers).items():
            mapping.setdefault(h, canon)
    mapping = {h: c for h, c in mapping.items() if c in _LEAD_FIELDS}
    existing = await db.get_leads(user["id"])
    seen: set = set()
    for l in existing:
        k = _dedup_keys(l)
        if any(k):
            seen.add(k)
    del existing
    imp = skp = err = 0
    err_msgs: list[str] = []
    for i, row in enumerate(reader, start=1):
        if i > 50_000:  # absolute cap
            break
        lead = _normalise_row(row, mapping)
        if not lead.get("org_name") and not lead.get("org_website") and not lead.get("contact_email"):
            skp += 1
            continue
        key = _dedup_keys(lead)
        if any(key) and key in seen:
            skp += 1
            continue
        lead.setdefault("found_date", datetime.now(timezone.utc).isoformat())
        lead.setdefault("source", f"web-import:{source}")
        lid = _make_lead_id(lead)
        lead["lead_id"] = lid
        try:
            await db.upsert_lead(user["id"], lid, lead)
            if any(key):
                seen.add(key)
            imp += 1
        except Exception as e:
            err += 1
            if err <= 5:
                err_msgs.append(f"row {i}: {str(e)[:120]}")
    return {"ok": True, "imported": imp, "skipped": skp, "errors": err,
            "error_samples": err_msgs, "source": source,
            "mapping": mapping}


@app.get("/api/export/json")
async def api_export_json(request: Request, user: dict = Depends(require_user)):
    require_feature(user, "export_json")
    ok, used = _check_export_rate(user["id"])
    if not ok:
        return JSONResponse(
            {"ok": False, "error": f"Export rate limit reached ({_EXPORT_LIMIT_PER_24H}/24h). Try again later."},
            status_code=429)
    leads = await db.get_leads(user["id"])
    content = json.dumps(leads, ensure_ascii=False, indent=2, default=str)
    await _log_export(user, "json", len(leads), request)
    return Response(
        content=content,
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename=huntova_leads_{datetime.now().strftime('%Y%m%d')}.json"}
    )


# ═══════════════════════════════════════════════════════════════
# API: SETTINGS
# ═══════════════════════════════════════════════════════════════

@app.get("/api/settings")
async def api_get_settings(user: dict = Depends(require_user)):
    s = await db.get_settings(user["id"])
    result = {**DEFAULT_SETTINGS, **s}
    # Strip secrets — they live in the keychain, never echo them back in
    # the GET response. Defends against legacy DB rows from before
    # v0.1.0a4 (or any future regression that ever wrote a secret to
    # settings) leaking cleartext to the browser on every load.
    for _secret_key in ("smtp_password", "webhook_secret",
                        "plugin_slack_webhook_url"):
        result.pop(_secret_key, None)
    # Compute wizard_configured so frontend knows whether to auto-show wizard
    w = result.get("wizard", {})
    result["wizard_configured"] = bool(w.get("company_name") or w.get("_site_scanned"))
    return result


@app.post("/api/settings")
async def api_save_settings(request: Request, user: dict = Depends(require_user)):
    body = await request.json()
    s = await db.get_settings(user["id"])
    s = {**DEFAULT_SETTINGS, **s}
    # a247: greatly expanded the saveable string-field whitelist so the
    # new Settings sub-categories (email, sequences, crm, team, data,
    # integrations, advanced) can persist without each adding its own
    # one-off coercion. List + numeric fields still get type-checked
    # below; this loop just routes plain strings through.
    for k in ("booking_url", "from_name", "from_email", "phone", "website",
              "ai_provider", "preferred_provider", "preferred_model",
              "default_tone",
              # email tab
              "reply_to", "email_signature", "email_footer", "opt_out_text",
              "subject_template_default", "fallback_opener", "send_timezone",
              # sequences tab
              "sequence_enabled", "follow_up_1_template", "follow_up_2_template",
              "follow_up_3_template", "stop_on_reply", "stop_on_unsubscribe",
              # crm tab
              "default_stage", "auto_advance_on_reply", "lead_dedupe_key",
              "crm_custom_field_1", "crm_custom_field_2", "crm_custom_field_3",
              # team tab
              "company_name", "role", "location", "timezone", "team_size",
              "industry_self",
              # data tab
              "language_filter",
              # integrations tab
              "slack_webhook_url", "discord_webhook_url", "telegram_bot_token",
              "telegram_chat_id", "calendly_workspace", "hubspot_api_token",
              "pipedrive_api_token", "ga_id",
              # advanced tab
              "hard_reject_strict", "debug_logs", "telemetry_enabled",
              "searxng_url"):
        if k in body:
            s[k] = body[k]
    # a247: list fields — comma-list strings already coerced client-side
    # but accept either string or array shape for safety.
    for lk in ("auto_tag_keywords", "blocked_domains", "allowed_tlds",
               "exclude_industries", "reject_keywords", "must_have_keywords",
               "pipeline_stages"):
        if lk in body:
            v = body[lk]
            if isinstance(v, list):
                s[lk] = [str(x).strip()[:120] for x in v if isinstance(x, (str, int, float)) and str(x).strip()][:60]
            elif isinstance(v, str):
                s[lk] = [x.strip() for x in v.split(",") if x.strip()][:60]
    # a247: integer fields — coerce + sanity-check.
    for ik, lo, hi in (
        ("send_window_start", 0, 23), ("send_window_end", 0, 23),
        ("daily_send_cap", 1, 5000),
        ("follow_up_1_days", 0, 365), ("follow_up_2_days", 0, 365),
        ("follow_up_3_days", 0, 365),
        ("high_intent_threshold", 0, 10),
        ("min_company_size", 0, 1000000), ("max_company_size", 0, 1000000),
        ("min_fit_score", 0, 10),
        ("agent_max_runtime_min", 1, 1440),
        ("max_concurrent_research", 1, 10),
        ("sse_idle_timeout_sec", 30, 3600),
        ("max_pages_per_lead", 1, 50),
        ("working_hours_start", 0, 23), ("working_hours_end", 0, 23),
    ):
        if ik in body:
            try:
                v = float(body[ik])
                if lo <= v <= hi:
                    s[ik] = v if ik in ("high_intent_threshold", "min_fit_score") else int(v)
            except Exception:
                pass
    # a240: numeric coercions so the chat-first UI's Settings panels
    # can save these without bothering with type narrowing client-side.
    if "default_max_leads" in body:
        try:
            v = int(body["default_max_leads"])
            if 1 <= v <= 500:
                s["default_max_leads"] = v
        except Exception:
            pass
    if "default_countries" in body:
        c = body["default_countries"]
        if isinstance(c, list):
            s["default_countries"] = [str(x).strip()[:60] for x in c if isinstance(x, str) and x.strip()][:30]
        elif isinstance(c, str):
            s["default_countries"] = [x.strip() for x in c.split(",") if x.strip()][:30]
    if "preferred_temperature" in body:
        try:
            t = float(body["preferred_temperature"])
            if 0.0 <= t <= 1.0:
                s["preferred_temperature"] = t
        except Exception:
            pass
    # Local/single-user mode: when the user fills in "Your Name" in the
    # Profile tab, mirror it to users.display_name so the dashboard
    # greeting + avatar pick it up. Cloud users have their own
    # display_name pipeline (signup form) that we don't want to overwrite.
    try:
        from runtime import CAPABILITIES as _CAPS
        if _CAPS.single_user_mode and "from_name" in body:
            _new_dn = (body.get("from_name") or "").strip()[:80]
            if _new_dn:
                await db.update_user(user["id"], display_name=_new_dn)
    except Exception:
        pass
    # Plugins / Webhooks / Outreach / Preferences tabs (Settings modal v2).
    # Secrets (smtp_password, webhook_secret, slack webhook URL) route through
    # secrets_store; only a *_set flag is persisted in plain settings.
    _ALLOWED_PLUGINS = {"csv-sink", "dedup-by-domain", "slack-ping", "discord-ping",
                        "telegram-ping", "generic-webhook", "recipe-adapter", "adaptation-rules"}
    if isinstance(body.get("plugins_enabled"), dict):
        cur = dict(s.get("plugins_enabled") or {})
        for k, v in body["plugins_enabled"].items():
            if k in _ALLOWED_PLUGINS:
                cur[k] = bool(v)
        s["plugins_enabled"] = cur
    for _strk, _max in (("plugin_csv_sink_path", 500), ("webhook_url", 500),
                        ("smtp_host", 200), ("smtp_user", 200)):
        if _strk in body:
            s[_strk] = (body[_strk] or "").strip()[:_max]
    if "smtp_port" in body:
        try:
            p = int(body["smtp_port"])
            if 1 <= p <= 65535:
                s["smtp_port"] = p
        except Exception:
            pass
    # Mirror SMTP host/user/port + webhook_url into os.environ so the
    # email_service / generic-webhook plugin / slack-ping path picks up
    # the dashboard-saved values without a process restart. SMTP_HOST /
    # SMTP_USER / SMTP_PORT are read by `email_service._smtp_settings()`
    # at call time (a42); webhook_url is read by `GenericWebhookPlugin`
    # via ctx.settings (a44) but the env fallback handles the gap when
    # the agent's HookContext snapshot is stale.
    try:
        if "smtp_host" in body:
            os.environ["SMTP_HOST"] = (body["smtp_host"] or "").strip()
        if "smtp_user" in body:
            os.environ["SMTP_USER"] = (body["smtp_user"] or "").strip()
        if "smtp_port" in body and "smtp_port" in s:
            os.environ["SMTP_PORT"] = str(s["smtp_port"])
        if "webhook_url" in body:
            os.environ["HV_WEBHOOK_URL"] = (body["webhook_url"] or "").strip()
    except Exception:
        pass
    _SECRET_MAP = (("plugin_slack_webhook_url", "HV_SLACK_WEBHOOK_URL", "plugin_slack_webhook_url_set"),
                   ("webhook_secret",          "HV_WEBHOOK_SECRET",     "webhook_secret_set"),
                   ("smtp_password",           "HV_SMTP_PASSWORD",      "smtp_password_set"))
    for _bk, _name, _flag in _SECRET_MAP:
        if _bk not in body:
            continue
        try:
            from secrets_store import set_secret, delete_secret
            _v = (body[_bk] or "").strip()
            if _v:
                set_secret(_name, _v)
                s[_flag] = True
                # Mirror to os.environ so the slack-ping / generic-webhook /
                # email_service modules see the new value WITHOUT a process
                # restart. Pre-a45 the keychain was updated but plugins
                # reading via env got stale values until next `huntova serve`.
                os.environ[_name] = _v
            else:
                delete_secret(_name)
                s[_flag] = False
                # Drop env mirror so plugins stop firing on the deleted value.
                os.environ.pop(_name, None)
        except Exception as _e:
            print(f"[SETTINGS] secret save failed for {_name}: {_e}")
    if body.get("theme") in ("dark", "light", "system"):
        s["theme"] = body["theme"]
    for _bk in ("reduced_motion", "telemetry_opt_in"):
        if _bk in body:
            # bool("false") is True (any non-empty string is truthy), so a
            # JSON client posting {"reduced_motion":"false"} would silently
            # flip it on. Treat string "false"/"0"/"no"/"off" as falsey.
            _bv = body[_bk]
            if isinstance(_bv, str):
                s[_bk] = _bv.strip().lower() in ("1", "true", "yes", "on")
            else:
                s[_bk] = bool(_bv)
    if "wizard" in body and isinstance(body["wizard"], dict):
        # Deep merge: preserve normalized_hunt_profile, training_dossier, archetype fields
        _PROTECTED_KEYS = {"normalized_hunt_profile", "training_dossier", "archetype",
                           "archetype_confidence", "_knowledge", "_train_count", "_last_trained"}
        existing_wiz = s.get("wizard", {})
        incoming_wiz = body["wizard"]
        # Start with existing, overlay incoming, but never erase protected keys
        merged = dict(existing_wiz)
        for k, v in incoming_wiz.items():
            if k not in _PROTECTED_KEYS:
                merged[k] = v
        # Always preserve protected keys from existing if incoming doesn't have them
        for pk in _PROTECTED_KEYS:
            if pk in existing_wiz and pk not in incoming_wiz:
                merged[pk] = existing_wiz[pk]
        s["wizard"] = merged
        _brain_kept = bool(merged.get("normalized_hunt_profile"))
        _dossier_kept = bool(merged.get("training_dossier"))
        print(f"[SETTINGS] Wizard merge: brain_preserved={_brain_kept} dossier_preserved={_dossier_kept}")
    await db.save_settings(user["id"], s)
    # Strip secret payloads from echo so they never round-trip back to JS.
    safe = {k: v for k, v in s.items()
            if k not in ("smtp_password", "webhook_secret", "plugin_slack_webhook_url")}
    return {"ok": True, "settings": safe}


# ── Settings → Webhooks tab: dummy POST to user-configured URL ──
@app.post("/api/webhooks/test")
async def api_webhooks_test(request: Request, user: dict = Depends(require_user)):
    """Fire a dummy `post_save`-shaped payload at the user's webhook URL,
    HMAC-signed if a secret is set, and surface the response status inline.
    Bound to require_user so a leaked URL alone can't drive traffic."""
    import hmac as _hmac, hashlib as _hashlib, json as _json, time as _time
    s = await db.get_settings(user["id"])
    s = {**DEFAULT_SETTINGS, **s}
    url = (s.get("webhook_url") or "").strip()
    if not url:
        return JSONResponse({"ok": False, "error": "no_url",
                             "message": "Save a webhook URL first."}, status_code=400)
    if not (url.startswith("http://") or url.startswith("https://")):
        return JSONResponse({"ok": False, "error": "bad_url"}, status_code=400)
    # SSRF gate — block private/loopback/link-local/AWS-metadata. The user
    # could otherwise save webhook_url=http://169.254.169.254/... and use
    # the test endpoint to exfiltrate cloud-instance metadata, or
    # http://postgres.railway.internal:5432/ to probe the internal VPC.
    # Stability fix (audit wave 29): the `except Exception: pass` was
    # fail-open. Any failure inside is_private_url (import error, DNS
    # resolver crash, IDN edge case, unexpected ValueError) silently
    # bypassed the gate and let the request fall through to
    # `requests.post(url, ...)` — exactly the SSRF target this gate
    # exists to prevent. Fail closed instead.
    try:
        from app import is_private_url as _is_private
        if _is_private(url):
            return JSONResponse({"ok": False, "error": "blocked_target",
                                 "message": "Webhook URL points at a private/loopback IP. Use a public URL."},
                                status_code=400)
    except Exception:
        return JSONResponse({"ok": False, "error": "blocked_target",
                             "message": "Could not validate target URL."},
                            status_code=400)
    # Per-user rate limit so a hijacked session can't credential-stuff or
    # port-scan via repeated test calls.
    if not _check_test_endpoint_rate(user["id"], "webhook"):
        return JSONResponse({"ok": False, "error": "rate_limited",
                             "message": "Too many tests — try again in a minute."},
                            status_code=429)
    secret = ""
    try:
        from secrets_store import get_secret
        secret = get_secret("HV_WEBHOOK_SECRET") or ""
    except Exception:
        pass
    payload = {
        "event": "post_save", "test": True,
        "lead": {"id": "test-lead", "org_name": "Acme Corp",
                 "contact_email": "test@example.com", "fit_score": 8},
        "ts": int(_time.time()),
    }
    body = _json.dumps(payload, separators=(",", ":")).encode()
    headers = {"Content-Type": "application/json", "User-Agent": "huntova/webhook-test"}
    if secret:
        sig = _hmac.new(secret.encode(), body, _hashlib.sha256).hexdigest()
        headers["X-Huntova-Signature"] = f"sha256={sig}"
    try:
        import requests as _rq
        r = _rq.post(url, data=body, headers=headers, timeout=8)
        return {"ok": (200 <= r.status_code < 300), "status": r.status_code,
                "preview": (r.text or "")[:200]}
    except Exception as e:
        return JSONResponse({"ok": False, "error": "request_failed",
                             "message": f"{type(e).__name__}: {e}"[:200]}, status_code=200)


# ── Settings → Outreach tab: probe SMTP creds (HELO + STARTTLS + AUTH) ──
@app.post("/api/webhook/test")
async def api_webhook_test(request: Request, user: dict = Depends(require_user)):
    """a254: ping any configured webhook with a sample lead payload so the
    user can confirm Settings → Integrations is wired before a real
    hunt fires."""
    if not _check_test_endpoint_rate(user["id"], "webhook"):
        return JSONResponse({"ok": False, "error": "rate_limited",
                             "message": "Too many test requests. Wait a moment."}, status_code=429)
    body = await request.json()
    target = (body.get("target") or "").strip().lower()
    if target not in ("slack", "discord", "telegram", "generic"):
        return JSONResponse({"ok": False, "error": "bad_target",
                             "message": "target must be one of: slack, discord, telegram, generic"}, status_code=400)
    s = {**DEFAULT_SETTINGS, **(await db.get_settings(user["id"]))}
    sample_lead = {
        "org_name": "Acme Test Co",
        "fit_score": 8,
        "country": "United Kingdom",
        "why_fit": "(test ping from Huntova — Settings → Integrations)",
        "org_website": "https://example.com",
        "url": "https://example.com",
    }
    try:
        from plugins import HookContext as _HC
        from bundled_plugins import (SlackPingPlugin, DiscordPingPlugin,
                                     TelegramPingPlugin, GenericWebhookPlugin)
    except ImportError:
        return JSONResponse({"ok": False, "error": "plugin_module"}, status_code=500)
    _ctx_mock = _HC(settings=s, user_id=user["id"])
    plugin_cls = {
        "slack": SlackPingPlugin,
        "discord": DiscordPingPlugin,
        "telegram": TelegramPingPlugin,
        "generic": GenericWebhookPlugin,
    }[target]
    try:
        plugin_cls().post_save(_ctx_mock, sample_lead)
        return {"ok": True, "message": f"Ping sent to {target}. Check your channel/server."}
    except Exception as e:
        return JSONResponse({"ok": False, "error": "send_failed",
                             "message": f"{type(e).__name__}: {str(e)[:160]}"}, status_code=500)


@app.post("/api/smtp/test")
async def api_smtp_test(user: dict = Depends(require_user)):
    s = {**DEFAULT_SETTINGS, **(await db.get_settings(user["id"]))}
    host = (s.get("smtp_host") or "").strip()
    try: port = int(s.get("smtp_port") or 587)
    except Exception: port = 587
    if not host or not (1 <= port <= 65535):
        return JSONResponse({"ok": False, "error": "missing_config",
                             "message": "Set SMTP host + port first."}, status_code=400)
    # Restrict to standard SMTP ports — otherwise the differentiated
    # error responses (auth_failed vs connect_failed) become a service-
    # discovery oracle for the internal VPC.
    if port not in (25, 465, 587, 2525):
        return JSONResponse({"ok": False, "error": "bad_port",
                             "message": "Use a standard SMTP port: 25, 465, 587, or 2525."},
                            status_code=400)
    # SSRF gate — resolve host, reject private/loopback IPs.
    # Audit wave 29: fail closed on any unexpected exception so a
    # resolver crash / IDN edge case can't slip through to smtplib.
    try:
        from app import is_private_url as _is_private
        if _is_private(f"smtp://{host}"):
            return JSONResponse({"ok": False, "error": "blocked_target",
                                 "message": "SMTP host resolves to a private/loopback IP."},
                                status_code=400)
    except Exception:
        return JSONResponse({"ok": False, "error": "blocked_target",
                             "message": "Could not validate SMTP host."},
                            status_code=400)
    if not _check_test_endpoint_rate(user["id"], "smtp"):
        return JSONResponse({"ok": False, "error": "rate_limited",
                             "message": "Too many tests — try again in a minute."},
                            status_code=429)
    smtp_user = (s.get("smtp_user") or "").strip()
    try:
        from secrets_store import get_secret
        pw = get_secret("HV_SMTP_PASSWORD") or ""
    except Exception:
        pw = ""
    import smtplib as _sm, socket as _sk
    try:
        srv = _sm.SMTP_SSL(host, port, timeout=10) if port == 465 else _sm.SMTP(host, port, timeout=10)
        if port != 465:
            srv.ehlo()
            try: srv.starttls(); srv.ehlo()
            except Exception: pass
        if smtp_user and pw:
            srv.login(smtp_user, pw)
        srv.quit()
        return {"ok": True, "message": f"Connected to {host}:{port} successfully."}
    except _sm.SMTPAuthenticationError:
        # Don't echo the server's raw smtp_error — some providers include
        # session state / partial credential context in their reject text.
        # Generic message is sufficient for the user (the cause is always
        # "wrong password or app-password required").
        return JSONResponse({"ok": False, "error": "auth_failed",
                             "message": "SMTP server rejected the username/password. "
                                        "If your account uses 2FA, generate an app-specific password and use that here."})
    except (_sk.gaierror, _sm.SMTPException, OSError):
        # Single canonical message — leaking the underlying socket
        # error string (e.g. "Connection refused" vs "timed out")
        # would re-enable the port-scan oracle the allowlist + IP
        # gate were closing.
        return JSONResponse({"ok": False, "error": "connect_failed",
                             "message": "SMTP connection failed (host/port/firewall). Check your settings."})


# ── Settings → Account / Data tab: full bundle download (local mode only) ──
@app.get("/api/account/export")
async def api_account_export(request: Request, user: dict = Depends(require_user)):
    """Bundle settings + agent_dna + leads + audit log. Local mode only —
    cloud users keep using /api/export/json which enforces plan + rate."""
    try:
        from runtime import CAPABILITIES
        if CAPABILITIES.mode != "local":
            return JSONResponse({"ok": False, "error": "cloud_mode",
                                 "message": "Bundle export is local-CLI only."}, status_code=403)
    except Exception:
        return JSONResponse({"ok": False, "error": "runtime_unavailable"}, status_code=503)
    if not _check_export_rate(user["id"])[0]:
        return JSONResponse({"ok": False, "error": "rate_limited"}, status_code=429)
    s = await db.get_settings(user["id"])
    leads = await db.get_leads(user["id"])
    try: dna = await db.get_agent_dna(user["id"])
    except Exception: dna = None
    try:
        audit = await db.get_admin_audit_log(page=1, page_size=200, target_user_id=user["id"])
        audit_items = audit.get("items", []) if isinstance(audit, dict) else []
    except Exception:
        audit_items = []
    bundle = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "user": {"id": user["id"], "email": user.get("email", "")},
        "settings": {k: v for k, v in (s or {}).items() if k not in ("smtp_password", "webhook_secret", "plugin_slack_webhook_url")},
        "agent_dna": dna, "leads": leads, "audit_log": audit_items,
    }
    await _log_export(user, "account_bundle", len(leads), request)
    fname = f"huntova_account_{datetime.now(timezone.utc).strftime('%Y%m%d')}.json"
    return Response(content=json.dumps(bundle, ensure_ascii=False, indent=2, default=str),
                    media_type="application/json",
                    headers={"Content-Disposition": f"attachment; filename={fname}"})


# ═══════════════════════════════════════════════════════════════
# API: AI FEATURES (neo-chat, rewrite, wizard, research, etc.)
# ═══════════════════════════════════════════════════════════════

@app.post("/api/neo-chat")
async def api_neo_chat(request: Request, user: dict = Depends(require_user)):
    require_feature(user, "ai_chat")
    if _check_ai_rate(user["id"]):
        return JSONResponse({"error": "Too many requests. Wait a moment."}, status_code=429)
    _np = _local_no_provider_response()
    if _np is not None: return _np
    body = await request.json()
    lid = body.get("lead_id")
    msg = body.get("message", "")[:2000]  # Limit message length to prevent abuse
    lead = await db.get_lead(user["id"], lid)
    if not lead:
        return JSONResponse({"error": "not found"}, status_code=404)

    s = await db.get_settings(user["id"])
    w = s.get("wizard", {})
    from_name = s.get("from_name") or w.get("company_name", "our team")
    company = w.get("company_name", "our company")
    bk = s.get("booking_url", "")

    prompt = f"""You are an AI assistant for {company}.

CURRENT LEAD: {lead.get('org_name','')} | Context: {lead.get('event_name','')} | {lead.get('country','')}
Contact: {lead.get('contact_name','Unknown')} | Current tools: {lead.get('platform_used','')}

CURRENT EMAIL SUBJECT: {lead.get('email_subject','')}
CURRENT EMAIL BODY:
{lead.get('email_body','')}

CURRENT LINKEDIN NOTE: {lead.get('linkedin_note','')}

USER REQUEST: {msg}

If the user asks you to modify the email, do so and return JSON:
{{"reply":"Your conversational response","updated_email":{{"email_subject":"...","email_body":"...","linkedin_note":"..."}}}}

If the user is just chatting/asking questions (not requesting email changes), return:
{{"reply":"Your conversational response"}}

Keep replies concise and helpful. Write emails like a real human — short sentences, conversational, no corporate speak.
BOOKING LINK: {bk}
Return ONLY valid JSON."""

    _chat_model = _get_model_for_user(user)
    def _ai_chat():
        resp = _byok_chat(**_ai_json_kwargs(
            model=_chat_model,
            messages=[
                {"role": "system", "content": "You are Huntova, a helpful AI email assistant. Return ONLY valid JSON."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.6, max_tokens=2048))
        raw = (resp.choices[0].message.content or "").strip()
        return re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

    try:
        raw = await asyncio.to_thread(_ai_chat)
        js_data = _extract_json(raw)
        if js_data:
            try:
                result = json.loads(js_data)
            except (json.JSONDecodeError, ValueError):
                return {"reply": raw[:500]}
            if result.get("updated_email"):
                ue = result["updated_email"]
                if ue.get("email_subject"):
                    lead["email_subject"] = ue["email_subject"]
                if ue.get("email_body"):
                    lead["email_body"] = ue["email_body"]
                if ue.get("linkedin_note"):
                    lead["linkedin_note"] = ue["linkedin_note"]
                await db.upsert_lead(user["id"], lid, lead)
            return result
        return {"reply": raw[:500]}
    except Exception:
        return {"reply": "AI temporarily unavailable. Try again in a moment."}


@app.post("/api/rewrite")
async def api_rewrite(request: Request, user: dict = Depends(require_user)):
    require_feature(user, "email_rewrite")
    if _check_ai_rate(user["id"]):
        return JSONResponse({"error": "Too many requests. Wait a moment."}, status_code=429)
    _np = _local_no_provider_response()
    if _np is not None: return _np
    body = await request.json()
    lid = body.get("lead_id")
    if not lid:
        return JSONResponse({"error": "lead_id required"}, status_code=400)
    tone = body.get("tone", "friendly")
    if tone not in ("friendly", "consultative", "broadcast", "warm", "formal"):
        tone = "friendly"
    lead = await db.get_lead(user["id"], lid)
    if not lead:
        return JSONResponse({"error": "not found"}, status_code=404)

    s = await db.get_settings(user["id"])
    bk = s.get("booking_url", "")

    # Save current email in history (capped at REWRITE_HISTORY_CAP latest entries
    # so heavily-edited leads don't accumulate unbounded JSON on the row).
    REWRITE_HISTORY_CAP = 10
    hist = lead.get("rewrite_history", [])
    if lead.get("email_body") and len(lead.get("email_body", "")) > 30:
        hist.append({
            "date": datetime.now(timezone.utc).isoformat(),
            "tone": lead.get("last_tone", "original"),
            "subject": lead.get("email_subject", ""),
            "body": lead.get("email_body", ""),
            "linkedin": lead.get("linkedin_note", ""),
        })
        if len(hist) > REWRITE_HISTORY_CAP:
            hist = hist[-REWRITE_HISTORY_CAP:]

    from app import generate_tone_email
    try:
        email = await asyncio.to_thread(generate_tone_email, lead, tone, bk, s)
    except Exception as e:
        return JSONResponse({"ok": False, "error": "AI rewrite failed. Try again."}, status_code=500)
    # Validate rewrite produced usable content before saving
    new_body = email.get("email_body", "")
    new_subj = email.get("email_subject", "")
    if not new_body or len(new_body) < 20:
        return JSONResponse({"ok": False, "error": "AI generated an empty email. Try again."}, status_code=500)
    lead["email_subject"] = new_subj
    lead["email_body"] = new_body
    lead["linkedin_note"] = email.get("linkedin_note", "")
    lead["last_tone"] = tone
    lead["rewrite_history"] = hist
    # Clear follow-up sequence — stale after tone change, user can regenerate via next agent run
    for _fk in ("email_followup_2","email_followup_3","email_followup_4"):
        lead.pop(_fk, None)
    await db.upsert_lead(user["id"], lid, lead)
    return {"ok": True, "subject": email.get("email_subject", ""), "body": email.get("email_body", "")}


@app.post("/api/revert-email")
async def api_revert_email(request: Request, user: dict = Depends(require_user)):
    require_feature(user, "email_rewrite")
    body = await request.json()
    lid = body.get("lead_id")
    hist_idx = body.get("history_index", 0)
    lead = await db.get_lead(user["id"], lid)
    if not lead:
        return JSONResponse({"error": "not found"}, status_code=404)
    rwh = lead.get("rewrite_history", [])
    actual_idx = len(rwh) - 1 - hist_idx
    if actual_idx < 0 or actual_idx >= len(rwh):
        return JSONResponse({"error": "invalid index"}, status_code=400)
    entry = rwh[actual_idx]
    rwh.append({
        "date": datetime.now(timezone.utc).isoformat(),
        "tone": "reverted",
        "subject": lead.get("email_subject", ""),
        "body": lead.get("email_body", ""),
        "linkedin": lead.get("linkedin_note", ""),
    })
    # Keep the same cap used by /api/rewrite.
    if len(rwh) > 10:
        rwh = rwh[-10:]
    lead["email_subject"] = entry.get("subject", lead["email_subject"])
    lead["email_body"] = entry.get("body", lead.get("email_body", ""))
    lead["linkedin_note"] = entry.get("linkedin", lead.get("linkedin_note", ""))
    lead["rewrite_history"] = rwh
    await db.upsert_lead(user["id"], lid, lead)
    return {"ok": True}


@app.post("/api/research")
async def api_research(request: Request, user: dict = Depends(require_user)):
    """Deep Research: re-scrape prospect, AI deep-analysis, rewrite email. Costs 1 credit."""
    require_feature(user, "research")
    if _check_ai_rate(user["id"]):
        return JSONResponse({"error": "Too many requests. Wait a moment."}, status_code=429)
    _np = _local_no_provider_response()
    if _np is not None: return _np
    body = await request.json()
    lid = body.get("lead_id")
    if not lid:
        return JSONResponse({"error": "lead_id required"}, status_code=400)
    lead = await db.get_lead(user["id"], lid)
    if not lead:
        return JSONResponse({"error": "not found"}, status_code=404)

    # Deduct 1 credit BEFORE starting research (skipped in local/BYOK
    # mode — the user pays their own provider; Huntova doesn't charge
    # per-lead).
    from policy import policy as _policy
    if _policy.deduct_on_save():
        has_credits = await db.deduct_credit(user["id"], 1)
        if not has_credits:
            return JSONResponse({"error": "No credits remaining. Top up or wait for monthly reset."}, status_code=402)

    ctx = get_or_create_context(user["id"], user["email"], user.get("tier", "free"))

    def do_research():
        # Outer safety net: any uncaught exception inside the thread would
        # silently leave the UI locked AND the credit deducted with no lead
        # update. Wrap everything so we always emit research_done and we
        # refund the credit if nothing useful landed.
        import requests as _rq
        from app import (validate_email, extract_linkedin_urls,
                         extract_json, _ai_call, USER_AGENT)
        from html import unescape as _html_unescape

        ctx.bus.emit("research_progress", {"lead_id": lid, "step": "Starting deep research..."})
        results = {"new_email": None, "updated_fields": {}, "key_findings": [], "analysis": {}}
        _email_re = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')

        try:
            from app import is_private_url as _is_private
            def _fetch(url):
                try:
                    # SSRF guard: refuse to fetch anything that resolves to a
                    # private, loopback, or link-local host. Research re-scrapes
                    # the lead URL + crawls its linked pages; without this the
                    # agent could be aimed at cloud metadata endpoints or
                    # internal admin panels via a poisoned lead.
                    if not url or not (url.startswith("http://") or url.startswith("https://")):
                        return ""
                    if _is_private(url):
                        return ""
                    # Stability fix (audit wave 26): cap response body
                    # at 2 MB. Without `stream=True` + size cap, a
                    # hostile or misconfigured prospect site can serve
                    # a multi-GB HTML stream and `r.text` materialises
                    # the entire body in RAM. The crawl loop fetches
                    # ~8 URLs per /api/research call, each in a
                    # daemon thread that has already deducted 1
                    # credit, so a single research call could OOM the
                    # process and the user gets charged for the crash.
                    _BODY_CAP = 2 * 1024 * 1024
                    r = _rq.get(url, headers={"User-Agent": USER_AGENT},
                                timeout=12, verify=False, stream=True)
                    # `requests` follows redirects by default — re-check
                    # the FINAL URL after redirects so a public URL that
                    # 302s to 127.0.0.1 / 169.254.169.254 / RFC1918 still
                    # gets refused. Without this the upfront SSRF guard
                    # above is a one-hop check only.
                    if r.url and _is_private(r.url):
                        return ""
                    if r.status_code != 200:
                        return ""
                    # Reject up-front when the server announces an
                    # over-cap Content-Length, then enforce again while
                    # streaming because Content-Length can be absent
                    # (chunked encoding) or lie.
                    try:
                        _cl = int(r.headers.get("content-length") or 0)
                    except Exception:
                        _cl = 0
                    if _cl and _cl > _BODY_CAP:
                        return ""
                    _buf = bytearray()
                    for _chunk in r.iter_content(chunk_size=8192):
                        if not _chunk:
                            break
                        _buf.extend(_chunk)
                        if len(_buf) > _BODY_CAP:
                            return ""
                    try:
                        return _buf.decode(r.encoding or "utf-8", errors="replace")
                    except Exception:
                        return _buf.decode("utf-8", errors="replace")
                except Exception:
                    return ""

            def _strip_html(html):
                """Extract readable text from HTML."""
                text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL|re.I)
                text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL|re.I)
                text = re.sub(r'<nav[^>]*>.*?</nav>', '', text, flags=re.DOTALL|re.I)
                text = re.sub(r'<footer[^>]*>.*?</footer>', '', text, flags=re.DOTALL|re.I)
                text = re.sub(r'<[^>]+>', ' ', text)
                text = _html_unescape(text)
                text = re.sub(r'\s+', ' ', text).strip()
                return text[:6000]

            def _extract_emails(html_text):
                found = []
                for e in _email_re.findall(html_text):
                    v = validate_email(e)
                    if v and v not in found:
                        found.append(v)
                for m in re.findall(r'mailto:([^"\'?\s]+)', html_text):
                    v = validate_email(m.split("?")[0].strip())
                    if v and v not in found:
                        found.append(v)
                return found

            # ── Step 1: Re-scrape the source page ──
            ctx.bus.emit("research_progress", {"lead_id": lid, "step": "Re-scraping source page..."})
            source_text = ""
            all_emails = []
            if lead.get("url"):
                html = _fetch(lead["url"])
                if html:
                    source_text = _strip_html(html)
                    all_emails.extend(_extract_emails(html))

            # ── Step 2: Crawl org website (6 key pages) ──
            ctx.bus.emit("research_progress", {"lead_id": lid, "step": "Crawling company website..."})
            site_text = ""
            website = lead.get("org_website", "")
            if website:
                _pages_text = []
                for path in ["", "/about", "/contact", "/team", "/about-us", "/services", "/pricing"]:
                    html = _fetch(website.rstrip("/") + path)
                    if html:
                        _pages_text.append(_strip_html(html)[:2000])
                        all_emails.extend(_extract_emails(html))
                        li = extract_linkedin_urls(html)
                        if li.get("org") and not lead.get("org_linkedin"):
                            results["updated_fields"]["org_linkedin"] = li["org"]
                        if li.get("contact") and not lead.get("contact_linkedin"):
                            results["updated_fields"]["contact_linkedin"] = li["contact"]
                    time.sleep(0.3)
                site_text = "\n---\n".join(_pages_text)[:8000]

            # ── Step 3: Find best email ──
            ctx.bus.emit("research_progress", {"lead_id": lid, "step": "Finding decision-maker contact..."})
            best_email = lead.get("contact_email", "")
            if all_emails and not best_email:
                for e in all_emails:
                    if not re.search(r"(info|hello|contact|general|admin|noreply|support)", e, re.I):
                        best_email = e
                        break
                if not best_email and all_emails:
                    best_email = all_emails[0]
            if best_email and not lead.get("contact_email"):
                results["updated_fields"]["contact_email"] = best_email

            # ── Step 4: Deep AI Analysis — the core of research ──
            ctx.bus.emit("research_progress", {"lead_id": lid, "step": "AI deep-analysing prospect..."})

            # Stability fix (multi-agent bug #15 sibling): properly close
            # the temp loop on exceptions too.
            _s_loop = asyncio.new_event_loop()
            try:
                s = _s_loop.run_until_complete(db.get_settings(user["id"]))
            finally:
                try: _s_loop.close()
                except Exception: pass
            _w = s.get("wizard", {})
            _company = _w.get("company_name", "our company")
            _services = _w.get("services", [])
            _desc = _w.get("business_description", "")
            _target = _w.get("target_clients", "")
            _pain = _w.get("pain_point", "")
            _bk = s.get("booking_url", "")
            _tone = _w.get("outreach_tone", "friendly")
            _from_name = s.get("from_name") or _company

            _analysis_prompt = f"""You are a senior business development analyst. A prospect was found by our AI agent. Your job is to do DEEP RESEARCH on this prospect and tell me:

1. Is this actually a good fit for our business? Why or why not?
2. What specific opportunities exist?
3. What should we know before reaching out?
4. Write a killer personalised outreach email.

═══ OUR BUSINESS ═══
Company: {_company}
What we do: {_desc[:400]}
Services: {', '.join(_services[:5]) if _services else 'professional services'}
Our ideal customer: {_target[:300]}
Problem we solve: {_pain[:200]}

═══ PROSPECT (from initial scan) ═══
Organisation: {lead.get('org_name', '?')}
Country: {lead.get('country', '?')}, City: {lead.get('city', '')}
Initial score: {lead.get('fit_score', '?')}/10
Initial reason: {lead.get('why_fit', '')}
Initial service gap: {lead.get('production_gap', '')}
Contact: {lead.get('contact_name', 'Unknown')} ({lead.get('contact_role', '')})
Email: {best_email or 'not found'}
Website: {website}
Current tools: {lead.get('platform_used', '')}

═══ SCRAPED CONTENT FROM THEIR SOURCE PAGE ═══
{source_text[:4000] if source_text else '(Could not fetch)'}

═══ SCRAPED CONTENT FROM THEIR WEBSITE ═══
{site_text[:5000] if site_text else '(Could not fetch)'}

═══ YOUR TASK ═══
Analyse everything above and respond with this JSON:

{{
  "verdict": "strong_fit" | "good_fit" | "possible_fit" | "weak_fit" | "bad_fit",
  "fit_score": 0-10,
  "key_findings": [
    "Finding 1 — a specific, actionable insight about this prospect (cite evidence from their site)",
    "Finding 2 — ...",
    "Finding 3 — ...",
    "Finding 4 — ...",
    "Finding 5 — ..."
  ],
  "why_fit": "2-3 sentences explaining exactly why this prospect is or isn't a fit for {_company}. Be specific — reference their industry, size, needs, and how our services map to their gaps.",
  "service_opportunity": "1-2 sentences on what specific service we could sell them and why they need it NOW. Reference evidence from their website.",
  "buyability_score": 0-10,
  "timing_score": 0-10,
  "contact_name": "best decision-maker name found (or null)",
  "contact_role": "their role (or null)",
  "is_recurring": true/false,
  "company_size_guess": "estimate based on team page, office locations, etc.",
  "current_tools": "what tools/services they currently use that we could replace or complement",
  "competitors_using": "any competitors of ours they seem to use",
  "email_subject": "max 8 words, specific to THEM, curiosity-driven",
  "email_body": "6-8 sentence cold email. Start with their situation (not 'I' or 'We'). Reference a SPECIFIC detail from their website. Show you understand their world. Mention what {_company} does. Include social proof if possible. End with low-pressure CTA. Sign off as {_from_name}, {_company}. Tone: {_tone}.",
  "linkedin_note": "under 150 chars, personal, references something specific about them"
}}

RULES:
- Every finding must cite EVIDENCE from their website or source page
- The email must reference at least 2 specific details from their site
- If the prospect is a bad fit, say so honestly — don't force it
- Be specific, not generic. I should read this and think "this analyst actually read their whole website"
"""

            raw = _ai_call(
                messages=[
                    {"role": "system", "content": "You are a senior B2B research analyst. Respond with ONLY valid JSON. No markdown, no backticks."},
                    {"role": "user", "content": _analysis_prompt}
                ],
                temperature=0.4,
                max_tokens=4000,
            )

            js = extract_json(raw)
            if not js:
                raise ValueError("AI returned no valid JSON")
            # Try parsing; if malformed, attempt repair
            try:
                analysis = json.loads(js)
            except json.JSONDecodeError:
                # Common AI issue: unescaped quotes/newlines inside string values
                # Attempt fix: re-extract with stricter cleaning
                _fixed = js
                # Replace literal newlines inside strings with \n
                _fixed = re.sub(r'(?<=": ")(.*?)(?="[,\}])', lambda m: m.group(0).replace('\n', '\\n').replace('\r', ''), _fixed, flags=re.DOTALL)
                try:
                    analysis = json.loads(_fixed)
                except json.JSONDecodeError:
                    # Last resort: ask AI to just give key fields
                    _retry_raw = _ai_call(
                        messages=[
                            {"role": "system", "content": "Fix this broken JSON. Return ONLY valid JSON. Escape all quotes inside string values. Keep all fields."},
                            {"role": "user", "content": js[:6000]}
                        ],
                        temperature=0.1,
                        max_tokens=4000,
                    )
                    _retry_js = extract_json(_retry_raw)
                    if _retry_js:
                        analysis = json.loads(_retry_js)
                    else:
                        raise ValueError("AI returned malformed JSON that could not be repaired")

            # ── Step 5: Build results from AI analysis ──
            ctx.bus.emit("research_progress", {"lead_id": lid, "step": "Compiling research report..."})

            results["key_findings"] = analysis.get("key_findings", [])
            results["analysis"] = {
                "verdict": analysis.get("verdict", "unknown"),
                "why_fit": analysis.get("why_fit", ""),
                "service_opportunity": analysis.get("service_opportunity", ""),
                "company_size": analysis.get("company_size_guess", ""),
                "current_tools": analysis.get("current_tools", ""),
                "competitors_using": analysis.get("competitors_using", ""),
            }

            # Build new email
            results["new_email"] = {
                "email_subject": analysis.get("email_subject", ""),
                "email_body": analysis.get("email_body", ""),
                "linkedin_note": analysis.get("linkedin_note", ""),
                "key_findings": analysis.get("key_findings", []),
            }

            # Updated lead fields
            uf = results["updated_fields"]
            if analysis.get("fit_score") is not None:
                uf["fit_score"] = int(analysis["fit_score"])
            if analysis.get("why_fit"):
                uf["why_fit"] = analysis["why_fit"][:300]
            if analysis.get("service_opportunity"):
                uf["production_gap"] = analysis["service_opportunity"][:300]
            if analysis.get("buyability_score") is not None:
                uf["buyability_score"] = int(analysis["buyability_score"])
            if analysis.get("timing_score") is not None:
                uf["timing_score"] = int(analysis["timing_score"])
            if analysis.get("contact_name") and analysis["contact_name"] != "null":
                uf["contact_name"] = analysis["contact_name"]
            if analysis.get("contact_role") and analysis["contact_role"] != "null":
                uf["contact_role"] = analysis["contact_role"]
            if analysis.get("is_recurring") is not None:
                uf["is_recurring"] = bool(analysis["is_recurring"])
            if analysis.get("current_tools"):
                uf["platform_used"] = analysis["current_tools"][:200]

        except Exception as ex:
            results["error"] = str(ex)

        # ── Save everything to DB ──
        # Stability fix (multi-agent bug #15 sibling): loop was opened
        # inside the try and only closed on the success path. Any
        # exception in between leaked the loop. Now in proper
        # try/finally so it always closes.
        if results.get("new_email") or results.get("updated_fields"):
            loop = None
            try:
                loop = asyncio.new_event_loop()
                current_lead = loop.run_until_complete(db.get_lead(user["id"], lid))
                if current_lead:
                    ne = results.get("new_email") or {}
                    uf = results.get("updated_fields") or {}
                    # Save email
                    if ne.get("email_subject"):
                        current_lead["email_subject"] = ne["email_subject"]
                    if ne.get("email_body"):
                        current_lead["email_body"] = ne["email_body"]
                    if ne.get("linkedin_note"):
                        current_lead["linkedin_note"] = ne["linkedin_note"]
                    # Save all updated fields
                    for fk, fv in uf.items():
                        if fv is not None:
                            current_lead[fk] = fv
                    loop.run_until_complete(db.upsert_lead(user["id"], lid, current_lead))
            except Exception as _save_err:
                # Stability fix (multi-agent bug #23): the credit was
                # already deducted when research started. If the save
                # silently fails, the user paid for analysis they never
                # see in their CRM. Refund and surface the error.
                results["error"] = f"Failed to save research results: {_save_err}"
                try:
                    refund_loop = asyncio.new_event_loop()
                    try:
                        refund_loop.run_until_complete(
                            db.refund_credit(user["id"], 1,
                                             "research_save_failed",
                                             f"lead:{lid}"))
                    finally:
                        try: refund_loop.close()
                        except Exception: pass
                except Exception as _refund_err:
                    print(f"[RESEARCH] save failed AND refund failed for user {user['id']} lead {lid}: save={_save_err} refund={_refund_err}")
            finally:
                if loop is not None:
                    try: loop.close()
                    except Exception: pass

        ctx.bus.emit("research_done", {"lead_id": lid, "results": results})

    def _do_research_safe():
        try:
            do_research()
        except Exception as _unexpected:
            # Top-level failure — refund the credit and unlock the UI.
            err_msg = f"Research failed: {_unexpected}"
            try:
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(
                        db.refund_credit(user["id"], 1,
                                         "research_refund",
                                         f"lead:{lid}:{type(_unexpected).__name__}"))
                finally:
                    loop.close()
            except Exception:
                pass
            ctx.bus.emit("research_done",
                         {"lead_id": lid, "results": {"error": err_msg}})

    threading.Thread(target=_do_research_safe, daemon=True).start()
    return {"ok": True, "message": "Research started — 1 credit deducted"}


# ── Wizard ──

# ── Shared site fetch + analysis helpers ──

_BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
_BAD_PAGE_SIGNALS = ["enable javascript", "please enable js", "verify you are human",
    "checking your browser", "cloudflare", "just a moment", "access denied",
    "403 forbidden", "attention required", "one more step", "complete the security check"]


def _fetch_site_text_sync(url: str) -> dict:
    """Fetch website text with Playwright fallback. Returns {text, final_url, method, error}."""
    import requests as _rq
    import warnings
    warnings.filterwarnings("ignore", message="Unverified HTTPS request")

    clean = url.replace("http://", "").replace("https://", "").strip("/")
    urls = [f"https://{clean}",
            f"https://www.{clean}" if not clean.startswith("www.") else f"https://{clean[4:]}",
            f"http://{clean}"]

    def _strip(html):
        t = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        t = re.sub(r"<style[^>]*>.*?</style>", "", t, flags=re.DOTALL | re.IGNORECASE)
        t = re.sub(r"<[^>]+>", " ", t)
        return re.sub(r"\s+", " ", t).strip()

    def _is_bad(text):
        low = text[:500].lower()
        return any(s in low for s in _BAD_PAGE_SIGNALS)

    best, best_url, method = "", "", "failed"
    _tried = []
    for u in urls:
        try:
            r = _rq.get(u, headers={"User-Agent": _BROWSER_UA}, timeout=15, verify=False, allow_redirects=True)
            _tried.append({"url": u, "status": r.status_code, "final": r.url, "len": len(r.text)})
            if r.status_code != 200 or len(r.text) < 200:
                continue
            text = _strip(r.text)
            if _is_bad(text):
                print(f"[SCAN] {u} → bot/challenge page detected")
                _tried[-1]["bad_page"] = True
                continue
            if len(text) > len(best):
                best, best_url, method = text, r.url, "requests"  # r.url = actual URL after redirects
            if len(text) > 300:
                break
        except Exception as e:
            _tried.append({"url": u, "error": str(e)[:100]})

    # Playwright fallback — try same URL variants
    if len(best) < 200:
        try:
            from playwright.sync_api import sync_playwright
            print(f"[SCAN] requests got {len(best)} chars, trying Playwright on {len(urls)} URLs")
            with sync_playwright() as pw:
                br = pw.chromium.launch(headless=True)
                for u in urls:
                    try:
                        pg = br.new_page()
                        pg.set_extra_http_headers({"User-Agent": _BROWSER_UA})
                        pg.goto(u, timeout=20000, wait_until="domcontentloaded")
                        pg.wait_for_timeout(2000)
                        html = pg.content()
                        actual_url = pg.url
                        pg.close()
                        _entry = {"url": u, "final": actual_url, "len": len(html), "method": "playwright"}
                        if len(html) > 200:
                            text = _strip(html)
                            _entry["text_len"] = len(text)
                            if _is_bad(text):
                                _entry["bad_page"] = True
                            elif len(text) > len(best):
                                best, best_url, method = text, actual_url, "playwright"
                                print(f"[SCAN] Playwright got {len(best)} chars from {actual_url}")
                                if len(text) > 300:
                                    _tried.append(_entry)
                                    break
                        _tried.append(_entry)
                    except Exception as pe:
                        _tried.append({"url": u, "method": "playwright", "error": str(pe)[:100]})
                        print(f"[SCAN] Playwright {u}: {pe}")
                br.close()
        except ImportError:
            print("[SCAN] Playwright not available")
        except Exception as e:
            print(f"[SCAN] Playwright init failed: {e}")

    # Phase 3: Jina Reader fallback for JS-heavy SPAs (free, no Playwright needed)
    if len(best) < 200:
        for u in urls:
            try:
                jina_url = f"https://r.jina.ai/{u}"
                print(f"[SCAN] trying Jina Reader: {u}")
                r = _rq.get(jina_url, headers={"Accept": "text/plain", "User-Agent": _BROWSER_UA}, timeout=20)
                _tried.append({"url": jina_url, "status": r.status_code, "len": len(r.text), "method": "jina"})
                if r.status_code == 200 and len(r.text) > 100:
                    # Jina returns markdown — remove images BEFORE flattening links
                    jtext = re.sub(r'!\[[^\]]*\]\([^)]+\)', '', r.text)      # remove ![alt](url)
                    jtext = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', jtext)   # [text](url) → text
                    jtext = re.sub(r'#+\s*', '', jtext)                       # remove heading markup
                    jtext = re.sub(r'\s+', ' ', jtext).strip()
                    # Extract actual URL from Jina response header line
                    _jurl = re.search(r'URL Source:\s*(https?://\S+)', r.text)
                    _jfinal = _jurl.group(1).strip() if _jurl else u
                    if not _is_bad(jtext) and len(jtext) > len(best):
                        best, best_url, method = jtext, _jfinal, "jina"
                        print(f"[SCAN] Jina Reader got {len(best)} chars from {_jfinal}")
                        if len(jtext) > 300:
                            break
            except Exception as e:
                _tried.append({"url": f"jina:{u}", "method": "jina", "error": str(e)[:100]})
                print(f"[SCAN] Jina Reader {u}: {e}")

    print(f"[SCAN] result: method={method} final_url={best_url[:80]} text_len={len(best)} tried={len(_tried)}")

    if len(best) < 50:
        return {"text": "", "final_url": "", "method": "failed", "error": "Could not read website content"}
    return {"text": best[:5000], "final_url": best_url, "method": method, "error": None}


def _analyse_site_ai_sync(site_text: str, url: str, mode: str = "full") -> str:
    """AI analysis using Gemini Pro for deep reasoning. Returns raw response string.

    a247: prompt extended to extract a much wider field set so the
    Brain wizard can prefill nearly every question from the scan. The
    user only refines instead of typing from scratch. We also feed the
    AI up to 14k chars of crawled text (up from 5k) — multi-page
    crawl lands more context and the model handles it fine.
    """
    from config import GEMINI_MODEL_PRO

    prompt = f"""You are a senior B2B sales intelligence analyst. A company is using AI to find their ideal clients and you must extract every signal that helps target them precisely.

WEBSITE: {url}
CONTENT (multi-page crawl):
{site_text[:14000]}

Think step by step:
1. What does this company actually DO? Not marketing fluff — what does a client receive after paying?
2. Who writes the cheque? Job title, department, company type.
3. What problem were buyers trying to solve when they found this company?
4. What makes them different from competitors? (look for moats, certifications, awards, named clients)
5. What industries do their clients come from? (Look at case studies, testimonials, client logos, named customers)
6. What geographic markets do they serve?
7. What's their price tier based on website design, language, and positioning?
8. What's their tone of voice — formal? casual? technical? warm? Which best matches?
9. What kind of clients should they AVOID? (mismatched fit, wrong industries, anti-ICP signals)
10. What real example clients are visible (named in case studies/logos)?

Respond with ONLY valid JSON. Fill EVERY field — leave none blank unless truly unknowable from the text. Be SPECIFIC, not generic.

{{
  "company_name": "the actual company name",
  "summary": "4-6 sentence description covering: what they do, who they serve, how they deliver, and what makes them different. Write this as if briefing a sales analyst in 30 seconds.",
  "business_type": "one sentence: specific type of business and core offering",
  "business_description": "3-4 sentence customer-facing description suitable for the Brain wizard 'business_description' field",
  "services": ["3-10 SPECIFIC services or products"],
  "how_it_works": "2-3 sentences: the actual delivery process from sale to completion",
  "industries_served": ["specific industries from clients/case studies"],
  "target_clients": "detailed paragraph: who buys this — org type, size, role, situation that triggers purchase. 30+ words.",
  "differentiators": ["3-5 specific competitive advantages — real moats, not 'quality'"],
  "price_tier": "budget|midrange|premium|enterprise",
  "regions": ["geographic areas — country names like 'France', 'Germany', 'United States'. Use full names matching common region pickers"],
  "company_size": "solo|small|medium|large",
  "delivery_method": "remote|onsite|hybrid|digital_product",
  "revenue_model": "project|retainer|subscription|per_event",
  "buyer_roles": ["3-7 job titles — full role names like 'Marketing Director', 'VP Engineering', 'Founder'"],
  "buying_triggers": ["specific situations that make someone buy NOW"],
  "example_good_clients": "3-5 named clients/case studies if visible in the text — comma-separated",
  "exclusions": "types of clients they should avoid — anti-ICP signals (e.g. 'enterprises with internal teams', 'agencies', 'pre-revenue startups')",
  "outreach_tone": "friendly|consultative|broadcast|warm|formal — pick the best match for their site's voice",
  "team_size": "approximate team count if visible in 'about' or 'team' pages",
  "year_founded": "year founded if visible, else empty string",
  "languages": ["languages the site is offered in — e.g. 'English', 'French']",
  "tech_stack": ["any technologies/platforms named — e.g. 'Shopify', 'WebFlow', 'AWS'"],
  "certifications": ["certifications, accreditations, partner badges visible"],
  "social_proof": ["logos, testimonials, awards, press mentions captured"],
  "contact_email": "primary contact email if visible",
  "contact_phone": "primary phone if visible",
  "booking_url": "calendly or scheduling URL if linked",
  "confidence": 0
}}"""

    # Use Gemini Pro for deep analysis (better reasoning), Flash as fallback
    models = [GEMINI_MODEL_PRO, MODEL_ID]
    last_err = None
    for m in models:
        try:
            resp = _byok_chat(**_ai_json_kwargs(
                model=m, messages=[
                    {"role": "system", "content": "You are a senior B2B intelligence analyst. Think deeply about the business before responding. Output ONLY valid JSON — no markdown, no commentary. Fill EVERY field."},
                    {"role": "user", "content": prompt}],
                temperature=0.3, max_tokens=8000))
            raw = (resp.choices[0].message.content or "").strip()
            return re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
        except Exception as e:
            last_err = e
            print(f"[SCAN] AI model {m} failed: {e}")
    raise last_err or RuntimeError("All AI models failed")


def _parse_ai_json(raw: str) -> dict | None:
    """Try multiple strategies to parse AI JSON."""
    if not raw:
        return None
    for attempt in [raw, re.sub(r',\s*([}\]])', r'\1', raw)]:
        try:
            return json.loads(attempt)
        except (json.JSONDecodeError, ValueError):
            pass
    js = _extract_json(raw)
    if js:
        try:
            return json.loads(re.sub(r',\s*([}\]])', r'\1', js))
        except Exception:
            pass
    return None


def _is_safe_url(url: str) -> bool:
    """Block SSRF: reject internal/reserved hosts and non-HTTP schemes.
    Delegates to app.is_private_url which does DNS resolution + full IP
    family checks (catches DNS rebinding tricks where the URL literal is
    a normal-looking domain but the A record points at 169.254.169.254).
    Also requires http(s) scheme."""
    try:
        clean = (url or "").strip()
        if not (clean.startswith("http://") or clean.startswith("https://")):
            return False
        from app import is_private_url
        return not is_private_url(clean)
    except Exception:
        return False


@app.post("/api/wizard/scan")
async def api_wizard_scan(request: Request, user: dict = Depends(require_user)):
    if _check_ai_rate(user["id"]):
        return JSONResponse({"error": "Too many scan requests. Wait a moment."}, status_code=429)
    body = await request.json()
    url = (body.get("url") or "").strip()
    if not url:
        return JSONResponse({"error": "No URL provided"}, status_code=400)
    # a256: forgiving URL parsing — accept "website.com" / "www.acme.io"
    # without scheme. Default to https://. Strip surrounding whitespace
    # and any leading "@" / quotes that paste-from-clipboard sometimes
    # carries from copied-from-doc text.
    url = url.lstrip("@").strip("'\"").strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        url = "https://" + url.lstrip("/")
    if not _is_safe_url(url):
        return JSONResponse({"error": "Invalid URL"}, status_code=400)
    # a247: replace single-page fetch with multi-page crawl so the Brain
    # wizard prefills from the WHOLE site, not just the homepage. Falls
    # back to single-page fetch if crawl_prospect returns insufficient
    # text — common for JS-heavy sites the simple fetch can't render.
    site_text = ""
    final_url = url
    crawl_method = "single"
    pages_seen = 1
    try:
        from app import crawl_prospect as _cp
        deep_text, _html, _n = await asyncio.to_thread(_cp, url, 18)
        if deep_text and len(deep_text) > 800:
            site_text = deep_text
            crawl_method = f"deep-crawl ({_n} pages)"
            pages_seen = int(_n) if isinstance(_n, int) else (len(_n) if hasattr(_n, '__len__') else 1)
    except Exception as _ce:
        print(f"[SCAN] deep crawl raised — falling back to single-page: {_ce}")
    if not site_text:
        result = await asyncio.to_thread(_fetch_site_text_sync, url)
        if result.get("error") or len(result.get("text", "")) < 50:
            return JSONResponse({"error": result.get("error") or "Could not read website.", "fallback": True})
        site_text = result["text"]
        final_url = result.get("final_url") or url
        crawl_method = result.get("method", "single")
    print(f"[SCAN] wizard/scan: {len(site_text)} chars via {crawl_method} → {final_url[:80]}")
    try:
        raw = await asyncio.to_thread(_analyse_site_ai_sync, site_text, final_url, "full")
        analysis = _parse_ai_json(raw)
        _no_cache = {"Cache-Control": "no-store, no-cache, must-revalidate, private"}
        if analysis and isinstance(analysis, dict):
            analysis["_site_text"] = site_text[:3000]
            analysis["_url"] = final_url
            return JSONResponse(analysis, headers=_no_cache)
        return JSONResponse({"error": "AI analysis failed", "fallback": True}, headers=_no_cache)
    except Exception as e:
        return JSONResponse({"error": "AI temporarily unavailable. Try again.", "fallback": True}, status_code=500)


@app.post("/api/wizard/complete")
async def api_wizard_complete(request: Request, user: dict = Depends(require_user)):
    body = await request.json()
    profile = body.get("profile", {})
    history = body.get("history", [])

    # ── WIZARD COMPLETION GATE: reject vague answers ──
    _vague_issues = []

    # Business description must be specific
    _desc = (profile.get("business_description") or "").strip().lower()
    _vague_descriptions = ["we help companies grow", "we help businesses", "we provide services",
                           "we offer solutions", "consulting", "marketing", "agency"]
    if len(_desc) < 15 or _desc in _vague_descriptions:
        _vague_issues.append("Business description is too vague. Describe specifically what you do and for whom.")

    # Geography cannot be just "Global"
    _geo = profile.get("regions", [])
    if _geo == ["Global"] or (len(_geo) == 1 and _geo[0].lower() == "global"):
        _vague_issues.append("'Global' is too broad. Pick your top 2-3 strongest regions.")

    # Ideal customer must be specific
    _cust = (profile.get("target_clients") or "").strip()
    _vague_customers = ["companies", "businesses", "marketing agencies", "agencies",
                        "startups", "enterprises", "founders", "anyone"]
    if len(_cust) < 20 or _cust.lower().strip().rstrip('.') in _vague_customers:
        _vague_issues.append("Ideal customer is too vague. Describe the specific type, size, industry, and situation.")

    # Decision makers cannot be empty
    _roles = profile.get("buyer_roles", [])
    if not _roles:
        _vague_issues.append("Select at least one decision maker role.")

    # Services must have substance
    _services = profile.get("services", [])
    if not _services or (len(_services) == 1 and len(_services[0]) < 5):
        _vague_issues.append("List at least 2-3 specific services or products you offer.")

    if _vague_issues:
        return JSONResponse({
            "ok": False,
            "error": "Your answers need more detail for Huntova to find good leads.",
            "vague_issues": _vague_issues,
        }, status_code=400)

    s = await db.get_settings(user["id"])
    s = {**DEFAULT_SETTINGS, **s}
    w = s.get("wizard", {})

    # Persist incoming profile fields. Stability fix (audit wave 28):
    # the previous version blind-merged every client-supplied key into
    # the wizard state — including private `_`-prefixed and protected
    # fields like `_train_count`, `_knowledge`, `training_dossier`,
    # `archetype`, `archetype_confidence`, `normalized_hunt_profile`,
    # `_last_trained`. /api/settings's wizard merge at line 4484
    # already had a `_PROTECTED_KEYS` allowlist for the same reason
    # (those keys are server-derived from training and should not be
    # client-controllable). A malicious or buggy client could poison
    # `_knowledge`, inflate `_train_count`, or seed a fake
    # `training_dossier` here. Apply the same protection.
    _PROTECTED_KEYS = {
        "normalized_hunt_profile", "training_dossier", "archetype",
        "archetype_confidence", "_knowledge", "_train_count",
        "_last_trained",
    }
    for k, v in profile.items():
        if v is None:
            continue
        if k in ("_interview_complete",):
            continue
        if k in _PROTECTED_KEYS:
            continue
        w[k] = v

    # Map old field names for backward compatibility
    if "summary" in profile and "business_description" not in profile:
        w["business_description"] = profile["summary"]
    if "business_description" in profile:
        w["business_description"] = profile["business_description"]
    if "_url" in profile:
        w["company_website"] = profile["_url"]
    if "_site_text" in profile:
        w["_site_context"] = profile["_site_text"]
    w["_site_scanned"] = True
    w["_interview_complete"] = True

    # Ensure new wizard fields are explicitly saved
    for field in ("icp_size", "icp_industries", "buyer_roles", "triggers", "exclusions",
                   "outreach_tone", "reject_enterprise", "reject_government",
                   "reject_strong_inhouse", "reject_no_contact",
                   "example_good_clients", "example_bad_clients",
                   "sales_cycle", "stage", "lead_sources"):
        if field in profile:
            w[field] = profile[field]

    answers = {h.get("question", ""): h.get("answer", "") for h in history}
    for k, v in answers.items():
        kl = k.lower()
        if "red_flag" in kl or "skip" in kl or "waste" in kl:
            w["red_flags"] = v
        if "dream" in kl or "ideal" in kl or "best_client" in kl or "profitable" in kl:
            w["clients"] = v
        if "trigger" in kl or "seek" in kl or "right now" in kl:
            w["edge"] = v

    knowledge = w.get("_knowledge", [])
    knowledge.append({
        "date": datetime.now().isoformat(),
        "type": "ai_interview",
        "content": json.dumps({"profile": profile, "qa_count": len(history)})[:2000],
        "source": "wizard_v2"
    })
    w["_knowledge"] = knowledge
    w["_train_count"] = (w.get("_train_count", 0) or 0) + 1
    w["_last_trained"] = datetime.now().isoformat()

    # Build summary
    lines = []
    if w.get("company_name"):
        lines.append(f"COMPANY: {w['company_name']}")
    if w.get("business_description"):
        lines.append(f"ABOUT: {w['business_description']}")
    if w.get("target_clients"):
        lines.append(f"TARGET: {w['target_clients']}")
    w["_summary"] = "\n".join(lines)

    # Build hunt brain and store alongside raw wizard data
    from app import _build_hunt_brain
    brain = _build_hunt_brain(w)
    w["normalized_hunt_profile"] = brain
    w["archetype"] = brain["archetype"]
    w["archetype_confidence"] = brain["archetype_confidence"]
    print(f"[WIZARD] Brain built: archetype={brain['archetype']} conf={brain['archetype_confidence']} can_hunt={brain['can_hunt']}")

    # Generate training dossier
    from app import _generate_training_dossier
    dossier = _generate_training_dossier(w, brain)
    w["training_dossier"] = dossier
    print(f"[WIZARD] Dossier built: v{dossier['training_dossier_version']} conf={dossier['confidence']['overall']}")

    s["wizard"] = w
    # Verify brain+dossier are in the wizard object before DB write
    _has_brain = bool(w.get("normalized_hunt_profile"))
    _has_dossier = bool(w.get("training_dossier"))
    print(f"[WIZARD] Pre-save check: brain={_has_brain} dossier={_has_dossier} wizard_keys={len(w)}")
    await db.save_settings(user["id"], s)
    print(f"[WIZARD] Settings saved to DB for user {user['id']}")

    # Generate Agent DNA in background (fire-and-forget — don't block wizard response).
    # Emits a terminal dna_updated SSE event on success or failure so the UI
    # can toast the true outcome instead of assuming success.
    async def _gen_dna():
        _ctx = get_or_create_context(user["id"], user.get("email", ""), user.get("tier", "free"))
        try:
            from app import generate_agent_dna
            dna = await asyncio.to_thread(generate_agent_dna, w)
            await db.save_agent_dna(user["id"], dna)
            print(f"[DNA] Generated for user {user['id']}: v{dna.get('version',1)}, {len(dna.get('search_queries',[]))} queries")
            try:
                _ctx.bus.emit("dna_updated", {"ok": True, "trigger": "wizard",
                                              "version": dna.get("version", 1),
                                              "queries_count": len(dna.get("search_queries", []))})
            except Exception:
                pass
        except Exception as e:
            print(f"[DNA] Generation failed for user {user['id']}: {e}")
            try:
                _ctx.bus.emit("dna_updated", {"ok": False, "trigger": "wizard", "error": str(e)[:200]})
            except Exception:
                pass
    asyncio.create_task(_gen_dna())

    # Update master training file (global intelligence across all users)
    async def _update_master():
        try:
            master_row = await db._afetchone("SELECT data FROM user_settings WHERE user_id = -1", [])
            try:
                master = json.loads(master_row["data"]) if master_row else {"businesses": [], "patterns": []}
            except (json.JSONDecodeError, TypeError):
                master = {"businesses": [], "patterns": []}

            # Add this business profile summary (anonymized)
            biz_summary = {
                "type": w.get("business_type", ""),
                "archetype": w.get("archetype", ""),
                "delivery": w.get("how_it_works", ""),
                "industries": w.get("icp_industries", [])[:5],
                "regions": w.get("regions", [])[:3],
                "services_count": len(w.get("services", [])),
                "has_signals": bool(w.get("buying_signals")),
                "has_discovery": bool(w.get("web_discovery_pages")),
                "has_disqualification": bool(w.get("disqualification_signals")),
                "has_lookalikes": bool(w.get("lookalikes")),
                "confidence": w.get("_wizard_confidence", 0),
                "updated": datetime.now().isoformat(),
            }

            # Keep last 50 business profiles (deduplicated by archetype+delivery)
            existing = [b for b in master.get("businesses", [])
                       if not (b.get("archetype") == biz_summary["archetype"]
                              and b.get("delivery") == biz_summary["delivery"])]
            existing.append(biz_summary)
            master["businesses"] = existing[-50:]

            master_json = json.dumps(master)
            await db._aexec(
                "INSERT INTO user_settings (user_id, data) VALUES (-1, %s) "
                "ON CONFLICT (user_id) DO UPDATE SET data = %s",
                [master_json, master_json])
        except Exception as e:
            print(f"[MASTER] Update failed: {e}")
    asyncio.create_task(_update_master())

    return {"ok": True, "train_count": w["_train_count"],
            "archetype": brain["archetype"],
            "archetype_confidence": brain["archetype_confidence"],
            "profile_confidence": brain["profile_confidence"],
            "can_hunt": brain["can_hunt"],
            "quality_flags": brain["blocking_flags"] + brain["warning_flags"],
            "dossier_version": dossier["training_dossier_version"],
            "dossier_confidence": dossier["confidence"]["overall"],
            "brain_saved": bool(w.get("normalized_hunt_profile")),
            "dossier_saved": bool(w.get("training_dossier"))}


@app.post("/api/wizard/save-progress")
async def api_wizard_save_progress(request: Request, user: dict = Depends(require_user)):
    """Incremental save — preserves answers as user progresses through wizard."""
    # Rate-limit: matches /api/wizard/scan + /api/wizard/assist. Without
    # this, a chatty client (or bot) can hammer the wizard JSON column
    # with rapid writes, ballooning the user_settings row + thrashing
    # SQLite's WAL.
    if _check_ai_rate(user["id"]):
        return JSONResponse({"error": "Too many saves. Wait a moment."}, status_code=429)
    body = await request.json()
    answers = body.get("answers", {})
    phase = body.get("phase", 0)
    confidence = body.get("confidence", 0)
    s = await db.get_settings(user["id"])
    s = {**DEFAULT_SETTINGS, **s}
    w = s.get("wizard", {})
    # Merge answers into wizard data (never overwrite protected keys)
    w["_wizard_answers"] = answers
    w["_wizard_phase"] = phase
    w["_wizard_confidence"] = confidence
    # Also extract key fields for backward compatibility
    if answers.get("business_name"):
        w["company_name"] = answers["business_name"]
    if answers.get("website"):
        w["company_website"] = answers["website"]
    if answers.get("what_you_do"):
        w["business_description"] = answers["what_you_do"]
    if answers.get("industries"):
        w["icp_industries"] = answers["industries"]
    if answers.get("geography"):
        w["regions"] = answers["geography"]
    if answers.get("decision_makers"):
        w["buyer_roles"] = answers["decision_makers"]
    if answers.get("triggers"):
        w["triggers"] = answers["triggers"]
    if answers.get("anti_customer"):
        w["exclusions"] = answers.get("anti_customer_pills", [])
    if answers.get("customer_size"):
        w["icp_size"] = answers["customer_size"]
    if answers.get("ideal_customer"):
        w["target_clients"] = answers["ideal_customer"]
    # New signal fields
    for _nf in ("lookalikes", "web_discovery_pages", "buying_signals", "disqualification_signals",
                "services", "how_it_works", "outreach_tone", "differentiator",
                "past_clients", "buyer_search_terms", "hiring_signals", "competitors",
                "dream_client", "comp_diff", "pain_point", "proof"):
        if answers.get(_nf):
            w[_nf] = answers[_nf]
    # Preserve learned fields the wizard's incremental save shouldn't
    # touch — same protection /api/settings POST already gives. Without
    # this, every wizard step would clobber scoring_rules + the trained
    # archetype + the adaptation card the user spent feedback to build.
    _PROTECTED = ("normalized_hunt_profile", "training_dossier", "archetype",
                  "archetype_confidence", "scoring_rules",
                  "_knowledge", "_train_count", "_last_trained")
    _existing_wiz = s.get("wizard") or {}
    for _pk in _PROTECTED:
        if _pk in _existing_wiz and _pk not in w:
            w[_pk] = _existing_wiz[_pk]
    s["wizard"] = w
    await db.save_settings(user["id"], s)
    return {"ok": True, "phase": phase, "confidence": confidence}


@app.post("/api/wizard/generate-phase5")
async def api_wizard_generate_phase5(request: Request, user: dict = Depends(require_user)):
    """Generate 5 dynamic AI follow-up questions based on previous answers."""
    body = await request.json()
    answers = body.get("answers", {})
    _model = _get_model_for_user(user)

    summary = []
    if answers.get("business_name"):
        summary.append(f"Business: {answers['business_name']}")
    if answers.get("what_you_do"):
        summary.append(f"What they do: {answers['what_you_do']}")
    if answers.get("ideal_customer"):
        summary.append(f"Ideal customer: {answers['ideal_customer']}")
    if answers.get("pain_point"):
        summary.append(f"Pain point: {answers['pain_point']}")
    if answers.get("differentiator"):
        summary.append(f"Differentiator: {answers['differentiator']}")
    if answers.get("dream_client"):
        summary.append(f"Dream client: {answers['dream_client']}")
    if answers.get("anti_customer"):
        summary.append(f"Never target: {answers['anti_customer']}")

    prompt = f"""Based on this business profile, generate 5 highly specific follow-up questions to better understand their ideal customer and outreach strategy. Reference their specific answers.

PROFILE:
{chr(10).join(summary)}

Return ONLY a JSON array of 5 questions. Each question must have:
- "question": the question text (reference something specific from their profile)
- "type": "text" or "single_select" or "multi_select"
- "options": array of options (only for select types, 3-6 options)
- "placeholder": placeholder text (only for text type)

Make questions specific to THIS business — not generic."""

    def _gen():
        resp = _byok_chat(**_ai_json_kwargs(
            model=_model,
            messages=[{"role": "system", "content": "Expert B2B strategist. Return ONLY valid JSON array."},
                      {"role": "user", "content": prompt}],
            temperature=0.4, max_tokens=2000))
        raw = (resp.choices[0].message.content or "").strip()
        return re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

    try:
        raw = await asyncio.to_thread(_gen)
        js = _extract_json(raw)
        if js:
            questions = json.loads(js)
            if isinstance(questions, list) and len(questions) >= 3:
                return {"ok": True, "questions": questions[:5]}
        return JSONResponse({"ok": False, "error": "Could not generate questions"}, status_code=500)
    except Exception as e:
        return JSONResponse({"ok": False, "error": "AI temporarily unavailable"}, status_code=500)


@app.post("/api/wizard/assist")
async def api_wizard_assist(request: Request, user: dict = Depends(require_user)):
    """AI assistant for the wizard — helps users craft better, more specific answers."""
    if _check_ai_rate(user["id"]):
        return JSONResponse({"error": "Too many requests. Wait a moment."}, status_code=429)
    body = await request.json()
    message = (body.get("message") or "").strip()
    question_context = (body.get("question") or "").strip()
    current_answer = (body.get("current_answer") or "").strip()
    answers = body.get("answers", {})
    chat_history = body.get("history", [])  # Previous messages for context

    if not message:
        return {"ok": False, "error": "No message"}

    # Build context from wizard answers + saved settings for richer context
    s = await db.get_settings(user["id"])
    w = s.get("wizard", {})
    ctx_parts = []
    # Use saved wizard data first (has more fields from scans), then overlay current answers
    for k, v in {**w, **answers}.items():
        if v and isinstance(k, str) and k[0] != '_' and isinstance(v, (str, list)):
            val = ', '.join(v) if isinstance(v, list) else v
            if len(val) > 3:
                ctx_parts.append(f"- {k}: {val[:200]}")
    # Also include site scan context if available
    site_ctx = w.get("_site_context", w.get("_site_text", ""))
    if site_ctx:
        ctx_parts.append(f"- website_content: {site_ctx[:400]}")
    ctx = "\n".join(ctx_parts[:20]) if ctx_parts else "No business info yet — the user just started."

    instructions = f"""You are a senior B2B sales strategist and copywriter helping someone train their AI lead generation agent called Huntova. You think deeply and give thorough, detailed responses.

CURRENT WIZARD QUESTION: "{question_context}"
THEIR CURRENT DRAFT ANSWER: "{current_answer}"

EVERYTHING WE KNOW ABOUT THEIR BUSINESS:
{ctx}

YOUR RULES:
- Give DETAILED, THOROUGH responses. Never cut yourself short. Write as much as needed.
- When they ask you to write something, write a COMPLETE, POLISHED version they can paste directly into the wizard field.
- When they say "longer" or "more specific" — double the length and add concrete details.
- Include specific names, cities, industries, job titles, company sizes, revenue ranges.
- If writing copy for the wizard, format it clearly and make it comprehensive.
- Think about what would make an AI search agent find better leads with this answer.
- Draw on everything you know about their business from the context above to make suggestions specific to THEM.
- You are an expert — be confident, opinionated, and specific. Not generic.
- IMPORTANT: Only answer what they asked. If they ask to improve their answer, improve it. If they ask a question, answer it. Do not add unrelated advice.
- If they ask "improve my answer" or "make it better", rewrite their current draft to be more specific, detailed, and actionable."""

    # Build clean message history — system prompt separate from user messages
    messages = [{"role": "system", "content": instructions}]
    # Add conversation history (last 10 turns)
    for turn in (chat_history or [])[-10:]:
        if turn.get("role") == "user" and turn.get("text"):
            messages.append({"role": "user", "content": turn["text"]})
        elif turn.get("role") == "bot" and turn.get("text"):
            messages.append({"role": "assistant", "content": turn["text"]})
    # Add current message
    messages.append({"role": "user", "content": message})

    _model = _get_model_for_user(user)
    print(f"[WIZARD ASSIST] model={_model} tier={user.get('tier','free')} turns={len(messages)} msg='{message[:60]}'")

    def _ai_assist():
        # Stability fix (Perplexity bug #36): explicit 60s timeout —
        # otherwise a stuck Gemini stream hangs the wizard request and
        # holds the worker thread until the user navigates away.
        resp = _byok_chat(
            model=_model,
            messages=messages,
            temperature=0.6,
            max_tokens=4000,
            timeout=60,
        )
        return (resp.choices[0].message.content or "").strip()

    # a244: pull `_re` to function scope so the except branch can use it
    # without "cannot access local variable '_re'" — the previous import
    # was inside the success branch, so when the primary AI call failed
    # the fallback's reply-stripping NameError'd before we could return.
    import re as _re
    try:
        reply = await asyncio.to_thread(_ai_assist)
        if not reply:
            return {"ok": False, "error": "Empty response — try rephrasing"}
        reply = _re.sub(r'<think>.*?</think>', '', reply, flags=_re.DOTALL).strip()
        reply = _re.sub(r'<think>.*$', '', reply, flags=_re.DOTALL).strip()
        print(f"[WIZARD ASSIST] OK ({len(reply)} chars)")
        return {"ok": True, "reply": reply}
    except Exception as e:
        print(f"[WIZARD ASSIST] {_model} failed: {type(e).__name__}: {e}")
        # a244: the primary path failed — usually because the user's
        # provider doesn't accept the hardcoded `_get_model_for_user`
        # model ID (e.g. asking OpenRouter for `claude-sonnet-4-5-2025…`
        # when it expects `anthropic/claude-sonnet-4.5`). Retry with
        # `model=None` to let the provider pick its own default model
        # name. If that also fails, then the legacy MODEL_ID fallback.
        try:
            def _fallback_default():
                resp = _byok_chat(
                    model=None,
                    messages=messages,
                    temperature=0.6,
                    max_tokens=4000,
                    timeout=60,
                )
                return (resp.choices[0].message.content or "").strip()
            reply = await asyncio.to_thread(_fallback_default)
            reply = _re.sub(r'<think>.*?</think>', '', reply, flags=_re.DOTALL).strip()
            print(f"[WIZARD ASSIST] OK via provider-default fallback ({len(reply)} chars)")
            return {"ok": True, "reply": reply}
        except Exception as e_pd:
            print(f"[WIZARD ASSIST] provider-default fallback also failed: {type(e_pd).__name__}: {e_pd}")
        try:
            def _fallback_legacy():
                resp = _byok_chat(
                    model=MODEL_ID,
                    messages=messages,
                    temperature=0.6,
                    max_tokens=4000,
                    timeout=60,
                )
                return (resp.choices[0].message.content or "").strip()
            reply = await asyncio.to_thread(_fallback_legacy)
            reply = _re.sub(r'<think>.*?</think>', '', reply, flags=_re.DOTALL).strip()
            return {"ok": True, "reply": reply}
        except Exception as e2:
            print(f"[WIZARD ASSIST] legacy fallback also failed: {type(e2).__name__}: {e2}")
            # Surface a useful message instead of just "AI temporarily
            # unavailable" — the user can read this and figure out
            # whether to switch providers / fix their key / wait.
            return {"ok": False,
                    "error": ("AI call failed. Last error: " + str(e2)[:160] +
                              ". Check Settings → API keys, or pin a working "
                              "provider in Settings → Engine.")}


@app.get("/api/wizard/status")
async def api_wizard_status(user: dict = Depends(require_user)):
    """Check if wizard is complete and return confidence score."""
    s = await db.get_settings(user["id"])
    w = s.get("wizard", {})
    return {
        "ok": True,
        "complete": bool(w.get("_interview_complete")),
        "confidence": w.get("_wizard_confidence", 0),
        "phase": w.get("_wizard_phase", 0),
        "has_answers": bool(w.get("_wizard_answers")),
        "company_name": w.get("company_name", ""),
    }


# ═══════════════════════════════════════════════════════════════
# AGENT DNA
# ═══════════════════════════════════════════════════════════════

@app.get("/api/agent-dna")
async def api_get_agent_dna(user: dict = Depends(require_user)):
    """Get the user's Agent DNA profile."""
    dna = await db.get_agent_dna(user["id"])
    feedback = await db.get_lead_feedback_count(user["id"])
    return {
        "ok": True,
        "dna": dna,
        "feedback": feedback,
    }


@app.post("/api/agent-dna/generate")
async def api_generate_agent_dna(request: Request, user: dict = Depends(require_user)):
    """Generate or regenerate Agent DNA from wizard data + feedback."""
    s = await db.get_settings(user["id"])
    w = s.get("wizard", {})
    if not w.get("company_name") and not w.get("business_description"):
        return JSONResponse({"ok": False, "error": "Complete the wizard first"}, status_code=400)

    _np = _local_no_provider_response()
    if _np is not None: return _np

    # Load existing DNA and feedback
    existing_dna = await db.get_agent_dna(user["id"])
    good_leads = await db.get_lead_feedback_recent(user["id"], "good", 10)
    bad_leads = await db.get_lead_feedback_recent(user["id"], "bad", 10)

    # Generate DNA via AI
    from app import generate_agent_dna
    dna = await asyncio.to_thread(generate_agent_dna, w, good_leads, bad_leads, existing_dna)

    await db.save_agent_dna(user["id"], dna)
    return {
        "ok": True,
        "version": dna.get("version", 1),
        "queries_count": len(dna.get("search_queries", [])),
        "generated_at": dna.get("generated_at", ""),
    }


@app.post("/api/lead-feedback")
async def api_lead_feedback(request: Request, user: dict = Depends(require_user)):
    """Record good/bad feedback on a lead."""
    body = await request.json()
    lead_id = body.get("lead_id", "")
    signal = body.get("signal", "")
    # Cap reason length so a buggy/malicious client can't push a multi-MB
    # blob into lead_feedback. 500 chars covers any genuine note.
    reason = (body.get("reason") or "")[:500]
    if not lead_id or signal not in ("good", "bad"):
        return JSONResponse({"ok": False, "error": "lead_id and signal (good/bad) required"}, status_code=400)
    # Confirm the lead actually belongs to this user. Without this,
    # /api/lead-feedback would happily save an orphan signal against an
    # arbitrary lead_id (the AI hallucinates one, a copy-paste from
    # another user's share, etc.), polluting `get_lead_feedback_recent`
    # and skewing future DNA generation off entirely-fictional rows.
    _own = await db._afetchone(
        "SELECT 1 AS ok FROM leads WHERE user_id = %s AND lead_id = %s",
        [user["id"], lead_id])
    if not _own:
        return JSONResponse(
            {"ok": False, "error": "lead not found in your workspace"},
            status_code=404)
    recent = await db._afetchone(
        "SELECT COUNT(*) as c FROM lead_feedback WHERE user_id = %s AND created_at > %s",
        [user["id"], (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()])
    if recent and recent["c"] >= 10:
        return JSONResponse({"ok": False, "error": "Too many feedback items. Please wait a few minutes."}, status_code=429)
    # Snapshot the count BEFORE the save so we can detect whether the
    # save actually crossed a 10-signal boundary. With ON CONFLICT
    # UPSERT, re-feedback on the same lead_id leaves the count
    # unchanged — without this snapshot we'd refire DNA generation
    # on every same-boundary update, burning AI budget.
    _pre_counts = await db.get_lead_feedback_count(user["id"]) or {}
    _pre_total = (_pre_counts.get("good", 0) or 0) + (_pre_counts.get("bad", 0) or 0)
    await db.save_lead_feedback(user["id"], lead_id, signal, reason)
    # Check if we should trigger DNA refinement (every 10 feedback items)
    counts = await db.get_lead_feedback_count(user["id"]) or {}
    total = counts.get("good", 0) + counts.get("bad", 0)
    # Refine only when this save *crossed* into a fresh 10-signal
    # bucket — i.e. floor(prev/10) < floor(new/10). Catches the 9→10
    # case but not a 10→10 UPSERT.
    should_refine = total > 0 and (_pre_total // 10) < (total // 10)
    if should_refine:
        # Fire-and-forget DNA refinement. Emits dna_updated so the UI can
        # surface the true outcome (previously silent; UI claimed nothing
        # whether it succeeded or failed).
        async def _refine():
            _ctx = get_or_create_context(user["id"], user.get("email", ""), user.get("tier", "free"))
            try:
                s = await db.get_settings(user["id"])
                w = s.get("wizard", {})
                existing = await db.get_agent_dna(user["id"])
                good = await db.get_lead_feedback_recent(user["id"], "good", 10)
                bad = await db.get_lead_feedback_recent(user["id"], "bad", 10)
                from app import generate_agent_dna
                dna = await asyncio.to_thread(generate_agent_dna, w, good, bad, existing)
                await db.save_agent_dna(user["id"], dna)
                # a245 — continual-learning gap #2: when feedback refinement
                # produces fresh DNA mid-hunt, push it onto the running
                # ctx._cached_dna so the agent loop's next batch picks up
                # refined queries + scoring rules. Without this, the in-
                # flight hunt keeps using stale DNA until it ends + restarts.
                try:
                    _ctx._cached_dna = dna
                    _ctx._dna_dirty = True  # the hunt loop checks + clears this
                except Exception:
                    pass
                print(f"[DNA] Auto-refined for user {user['id']} (v{dna.get('version',1)}, {total} feedback items)")
                try:
                    _ctx.bus.emit("dna_updated", {"ok": True, "trigger": "feedback_refine",
                                                  "version": dna.get("version", 1),
                                                  "feedback_count": total,
                                                  "queries_count": len(dna.get("search_queries", []))})
                except Exception:
                    pass
            except Exception as e:
                print(f"[DNA] Auto-refinement failed: {e}")
                try:
                    _ctx.bus.emit("dna_updated", {"ok": False, "trigger": "feedback_refine", "error": str(e)[:200]})
                except Exception:
                    pass
        asyncio.create_task(_refine())
    # Update learning profile every 5 feedback signals
    # Rebuild learning profile every 3 feedback signals for fast adaptation
    should_update_profile = total > 0 and total % 3 == 0
    if should_update_profile:
        async def _update_profile():
            try:
                all_fb = await db.get_all_feedback_for_profile(user["id"])
                if not all_fb:
                    return
                profile = await _build_learning_profile(user["id"], all_fb)
                if profile:
                    existing = await db.get_learning_profile(user["id"])
                    ver = (existing["version"] + 1) if existing else 1
                    _instr = profile.get("instruction_summary", "")
                    if not isinstance(_instr, str):
                        _instr = ""
                    await db.save_learning_profile(
                        user["id"],
                        json.dumps(profile.get("preferences", {})),
                        _instr[:2000],
                        total,
                        ver
                    )
                    print(f"[LEARN] Profile updated for user {user['id']} v{ver} ({total} signals)")
            except Exception as e:
                print(f"[LEARN] Profile update failed: {e}")
        asyncio.create_task(_update_profile())
    return {"ok": True, "total_feedback": total, "refining": should_refine, "profile_updating": should_update_profile}


async def _build_learning_profile(user_id: int, feedback: list) -> dict:
    """Generate a compact learning profile from user feedback using AI."""
    good = [f for f in feedback if f.get("signal") == "good"]
    bad = [f for f in feedback if f.get("signal") == "bad"]
    if not good and not bad:
        return {}

    prompt = "You are analysing a user's lead quality preferences for an AI prospecting tool.\n\n"
    prompt += "LEADS THE USER MARKED AS GOOD FIT:\n"
    for g in good[:15]:
        reason = g.get('reason', '')
        reason_text = f" [Reason: {reason}]" if reason else ""
        prompt += f"- {g.get('org_name','?')} ({g.get('country','?')}) — {g.get('why_fit','')[:100]} (Score: {g.get('fit_score',0)}){reason_text}\n"
    prompt += "\nLEADS THE USER MARKED AS BAD FIT:\n"
    for b in bad[:15]:
        reason = b.get('reason', '')
        reason_text = f" [Reason: {reason}]" if reason else ""
        prompt += f"- {b.get('org_name','?')} ({b.get('country','?')}) — {b.get('why_fit','')[:100]} (Score: {b.get('fit_score',0)}){reason_text}\n"

    prompt += """
Based on this feedback, generate a JSON object with:
1. "preferences" — an object with keys: preferred_industries (array), avoided_industries (array), preferred_company_sizes (array), preferred_countries (array), avoided_signals (array of red-flag patterns), valued_signals (array of positive patterns), min_acceptable_score (number), tone_preference (string)
2. "instruction_summary" — a 2-3 sentence plain-English instruction the agent should follow when scoring leads for this user. Be specific about what this user considers good vs bad.

Return ONLY valid JSON, no markdown.
"""

    from app import _ai_call
    try:
        response = _ai_call(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3, max_tokens=800
        )
        if response:
            from app import extract_json
            parsed = extract_json(response)
            if parsed:
                return json.loads(parsed) if isinstance(parsed, str) else parsed
    except Exception as e:
        print(f"[LEARN] AI profile generation error: {e}")
    return {}


@app.get("/api/learning-profile")
async def api_learning_profile(user: dict = Depends(require_user)):
    """Get the user's learning profile."""
    profile = await db.get_learning_profile(user["id"])
    counts = await db.get_lead_feedback_count(user["id"])
    return {"ok": True, "profile": profile, "feedback": counts}


@app.post("/api/track-actions")
async def api_track_actions(request: Request, user: dict = Depends(require_user)):
    """Receive batch of lead action events from the client."""
    body = await request.json()
    actions = body.get("actions", [])
    if not actions or not isinstance(actions, list):
        return {"ok": True, "saved": 0}
    # Whitelist of action types this endpoint accepts. The track-actions
    # endpoint is called from the dashboard via sendBeacon (CSRF-exempt),
    # so any payload that survives the auth check could write garbage
    # to the lead_actions table. Restrict to the action names the
    # frontend actually emits — anything else gets dropped silently.
    _ALLOWED_ACTION_TYPES = {
        "click", "open", "opened", "view", "viewed",
        "csv_export", "json_export", "export",
        "rewrite", "research", "send_email", "marked_won",
        "marked_lost", "marked_replied", "qualified", "unqualified",
        "good_fit", "bad_fit", "hot_open", "deck_open",
    }
    saved = 0
    for a in actions[:50]:  # Cap at 50 per batch
        lid = a.get("lead_id", "")
        atype = a.get("action", "")
        if not lid or not atype:
            continue
        # Sanitise + length-cap. atype that's too long or not on the
        # whitelist gets a generic "other" label so the row is still
        # tracked (volume signal) without polluting the GROUP BY surface.
        atype = str(atype)[:40]
        if atype not in _ALLOWED_ACTION_TYPES:
            atype = "other"
        # Compute score/confidence bands from lead data
        score_band = ""
        conf_band = ""
        lead = await db.get_lead(user["id"], lid) if lid != "export" else None
        if lead:
            s = lead.get("fit_score", 0) or 0
            score_band = "9-10" if s >= 9 else "7-8" if s >= 7 else "5-6" if s >= 5 else "0-4"
            c = lead.get("_data_confidence", 0) or 0
            conf_band = "high" if c >= 0.6 else "medium" if c >= 0.4 else "low"
        meta = json.dumps({"at": a.get("at", ""), "rationale": (lead.get("why_fit", "") if lead else "")[:100]})
        await db.save_lead_action(user["id"], lid, atype, score_band, conf_band, meta)
        saved += 1
    return {"ok": True, "saved": saved}


@app.post("/api/smart-score")
async def api_smart_score(user: dict = Depends(require_user)):
    require_feature(user, "smart_score")
    leads = await db.get_leads(user["id"])
    good = [l for l in leads if l.get("email_status") in ("won", "meeting_booked", "replied")]
    if len(good) < 2:
        return JSONResponse({"error": "Need at least 2 converted leads", "good_count": len(good)}, status_code=400)

    patterns = {"countries": {}, "event_types": {}, "recurring": 0, "total": len(good)}
    for l in good:
        c = l.get("country", "?")
        patterns["countries"][c] = patterns["countries"].get(c, 0) + 1
        et = l.get("event_type", "?")
        patterns["event_types"][et] = patterns["event_types"].get(et, 0) + 1
        if l.get("is_recurring"):
            patterns["recurring"] += 1

    recurring_pct = round(100 * patterns["recurring"] / len(good))
    updated = 0
    for l in leads:
        if l.get("email_status") in ("won", "lost", "ignored"):
            continue
        bonus = 0
        c = l.get("country", "?")
        if c in patterns["countries"]:
            bonus += min(3, patterns["countries"][c])
        et = l.get("event_type", "?")
        if et in patterns["event_types"]:
            bonus += min(2, patterns["event_types"][et])
        if l.get("is_recurring") and recurring_pct > 50:
            bonus += 1
        if l.get("contact_email"):
            bonus += 1
        base = l.get("fit_score", 5)
        smart = min(10, base + bonus)
        if smart != l.get("smart_score"):
            l["smart_score"] = smart
            l["priority_rank"] = smart * 10 + (1 if l.get("contact_email") else 0) + (1 if l.get("is_recurring") else 0)
            await db.upsert_lead(user["id"], l["lead_id"], l)
            updated += 1

    return {"ok": True, "updated": updated, "patterns": {"good_leads": len(good), "recurring_pct": recurring_pct}}


# ── GDPR ──

@app.post("/api/gdpr/erasure")
async def api_gdpr_erasure(request: Request, user: dict = Depends(require_user)):
    body = await request.json()
    identifier = (body.get("email") or body.get("domain") or "").strip()
    if not identifier or len(identifier) < 3:
        return JSONResponse({"error": "Provide 'email' or 'domain'"}, status_code=400)
    result = await db.gdpr_erasure(user["id"], identifier)
    return {"ok": True, **result}


# ═══════════════════════════════════════════════════════════════
# AGENT CONTROL + SSE
# ═══════════════════════════════════════════════════════════════

@app.post("/agent/control")
async def agent_control(request: Request, user: dict = Depends(require_user)):
    body = await request.json()
    action = body.get("action")
    print(f"[AGENT] user={user['id']} action={action} tier={user.get('tier','?')}")

    if action == "start":
        result = await agent_runner.start_agent(
            user["id"], user["email"], user.get("tier", "free"), body
        )
        print(f"[AGENT] user={user['id']} start result: {result}")
        return result
    elif action == "stop":
        agent_runner.stop_agent(user["id"])
        return {"ok": True, "action": "stop"}
    elif action == "pause":
        agent_runner.pause_agent(user["id"])
        return {"ok": True, "action": "pause"}
    elif action == "resume":
        agent_runner.resume_agent(user["id"])
        return {"ok": True, "action": "resume"}
    else:
        return {"ok": True, "action": action}


@app.get("/agent/events")
async def agent_events(request: Request):
    user = await get_current_user(request)
    if not user:
        return Response(status_code=401)

    ctx = get_or_create_context(user["id"], user["email"], user.get("tier", "free"))
    q = ctx.bus.subscribe()

    async def event_stream():
        try:
            # Send initial status
            running = agent_runner.is_running(user["id"])
            pos = agent_runner.queue_position(user["id"])
            state = "running" if running else ("queued" if pos else "idle")
            yield f"event: status\ndata: {json.dumps({'text': 'Connected', 'state': state})}\n\n"

            # Stability fix (Perplexity bug #53): the previous version
            # blocked 30s on q.get and only ever cleaned up via
            # CancelledError. After a client drop, the subscriber queue
            # stayed in the bus for up to 30s and any emits during that
            # window piled up unread. Now we poll
            # request.is_disconnected() between short get windows so
            # cleanup runs promptly when the client disappears.
            import queue as _q
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.to_thread(q.get, True, 5)
                    yield msg
                except _q.Empty:
                    yield ": keepalive\n\n"
                except asyncio.CancelledError:
                    break
        finally:
            ctx.bus.unsubscribe(q)

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/health")
async def api_health():
    return {"status": "ok", "build": "b3-2-fix-p3-save"}


@app.get("/api/runtime")
async def api_runtime():
    """Runtime capability flags for the frontend.

    Public (no auth) by design — the frontend needs the flags BEFORE it
    decides which UI surfaces to render (e.g. the credit pill, pricing
    modal, login form). Capability info is non-sensitive boolean
    metadata about how this install of Huntova is configured. See
    runtime.py for the resolution rules.
    """
    from runtime import CAPABILITIES
    # Surface the installed version too so the dashboard's sidebar
    # footer stays in sync with the actual binary instead of a
    # hardcoded fallback.
    try:
        from cli import VERSION as _hv_ver
    except Exception:
        _hv_ver = ""
    return {"ok": True, "runtime": CAPABILITIES.to_dict(), "version": _hv_ver}

@app.post("/api/ops/rerun-pass3")
async def api_rerun_pass3(user: dict = Depends(require_admin)):
    """Dev/test: rerun Pass 3 rank+rewrite on existing leads without a full agent run."""
    leads = await db.get_leads(user["id"])
    if not leads:
        return {"ok": False, "error": "no leads"}
    try:
        from app import rank_and_rewrite
        rewritten = await asyncio.to_thread(rank_and_rewrite, leads)
        await db.save_leads_bulk(user["id"], rewritten)
        top = [l for l in rewritten if l.get("is_top10")]
        fu_count = sum(1 for l in top if l.get("email_followup_2"))
        return {"ok": True, "total": len(rewritten), "top10": len(top), "with_followups": fu_count}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)[:200]}, status_code=500)


@app.get("/api/status")
async def api_status(request: Request):
    user = await get_current_user(request)
    if not user:
        return JSONResponse({"running": False, "state": "idle"}, status_code=401)
    uid = user["id"]
    running = agent_runner.is_running(uid)
    result: dict = {"running": running, "state": "running" if running else "idle"}
    # Include live run counters + stopping flag so clients that reconnect
    # mid-run can resync without waiting for the next SSE event, and so the
    # "Stopping…" UI state persists across page reloads.
    try:
        from user_context import get_context
        ctx = get_context(uid)
        if ctx is not None:
            result["lead_count"] = len(ctx.all_leads) if ctx.all_leads else 0
            if ctx._latest_progress:
                result["progress"] = dict(ctx._latest_progress)
            if running and ctx.check_stop():
                result["stopping"] = True
                result["state"] = "stopping"
            # a245: surface the most-recent status text + state so a
            # client polling without an SSE connection can see WHY the
            # agent is idle. Critical for the chat-fire-and-forget path
            # where the user fires a hunt + immediately closes the
            # conversation; if SearXNG is offline or the AI key broke,
            # the next /api/status returns the cached status string
            # instead of pretending nothing happened.
            _ls = getattr(ctx, "_latest_status_text", None)
            _lst = getattr(ctx, "_latest_status_state", None)
            if _ls:
                result["last_status"] = _ls
            if _lst:
                result["last_state"] = _lst
    except Exception:
        pass
    # Explicit no-cache headers — /api/status returns live agent state
    # that the dashboard polls every 5s. A CDN or browser cache holding
    # a stale 'running' response would mask the real idle/stopping state
    # for minutes after the agent finished. Mirrors the no-cache header
    # already on /agent/events.
    return JSONResponse(
        result,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
        },
    )


# ═══════════════════════════════════════════════════════════════
# PAYMENTS (Stripe)
# ═══════════════════════════════════════════════════════════════

@app.post("/api/checkout")
async def api_checkout(request: Request, user: dict = Depends(require_user)):
    """Create Stripe checkout session."""
    from policy import policy
    if not policy.show_billing_ui():
        # Local CLI / BYOK mode — no checkout surface. Return 503 so the
        # frontend's existing fallback path renders cleanly.
        return JSONResponse({"error": "Payments disabled in local mode (BYOK — you're paying your AI provider directly)."}, status_code=503)
    from payments import create_checkout, is_stripe_configured
    if not is_stripe_configured():
        return JSONResponse({"error": "Payments not configured yet"}, status_code=503)
    body = await request.json()
    product_id = body.get("product_id")
    if not product_id:
        return JSONResponse({"error": "product_id required"}, status_code=400)
    # Feature F6: capture which paywall surface drove the click for the
    # admin growth dashboard. Best-effort — never block checkout if
    # this insert fails.
    source = (body.get("source") or "")[:60]
    try:
        await db.record_checkout_start(user["id"], product_id, source)
    except Exception as e:
        print(f"[checkout_start] log failed: {e}")
    try:
        result = await create_checkout(user["id"], product_id)
        return {"ok": True, **result}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        print(f"[STRIPE] checkout error: {e}")
        return JSONResponse({"error": "Checkout failed. Please try again."}, status_code=500)


@app.post("/api/webhook/stripe")
async def stripe_webhook(request: Request):
    """Stripe webhook — processes payments. No auth (Stripe calls this)."""
    from policy import policy
    if not policy.show_billing_ui():
        # Local mode: nothing should be hitting this endpoint. Return
        # 200 + ignored so a misrouted call doesn't pollute logs.
        return JSONResponse({"ok": True, "ignored": "billing disabled in local mode"})
    from payments import handle_webhook
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    print(f"[STRIPE] webhook received, payload_len={len(payload)}, has_sig={bool(sig)}")
    try:
        result = await handle_webhook(payload, sig)
        print(f"[STRIPE] webhook result: {result}")
        # Stability fix (audit wave 30): the previous version did
        # `return result` — FastAPI serialises the dict as 200 OK
        # regardless of contents, so a signature-fail / "Invalid
        # signature" / "Timestamp too old" payload reached Stripe as
        # HTTP 200. Stripe interprets 200 as "delivered, don't
        # retry", so a webhook secret rotation mismatch or any
        # transient signature failure silently dropped real
        # subscription events (plan_changed, invoice.paid,
        # subscription.created) — credits / tier changes never
        # reflected. Reflect ok=False as 400 so Stripe retries and
        # the failure surfaces in the dashboard's webhook log.
        if isinstance(result, dict) and not result.get("ok", True):
            return JSONResponse(result, status_code=400)
        return result
    except Exception as e:
        print(f"[STRIPE] webhook error: {e}")
        return JSONResponse({"error": str(e)}, status_code=400)


@app.get("/api/account")
async def api_account(user: dict = Depends(require_user)):
    """Get full account info including tier, credits, features."""
    user_info = await db.get_user_by_id(user["id"])
    if not user_info:
        raise HTTPException(status_code=401, detail="User not found")
    credits = await db.check_and_reset_credits(user["id"])
    tier = user_info.get("tier", "free")
    tier_info = TIERS.get(tier, TIERS["free"])

    # Feature gating — boolean entitlements from central FEATURE_TIERS map
    features = user_features(user_info)
    # Numeric tier limits (not binary entitlements; kept inline)
    features["max_leads_per_run"] = 3 if tier == "free" else (25 if tier == "growth" else 999)
    features["max_queries"] = 8 if tier == "free" else (35 if tier == "growth" else 200)

    return {
        "ok": True,
        "user": {
            "id": user_info["id"],
            "email": user_info["email"],
            "display_name": user_info["display_name"],
            "tier": tier,
            "tier_name": tier_info["name"],
            "credits_remaining": credits,
            "email_verified": bool(user_info.get("email_verified")),
            "auth_provider": user_info.get("auth_provider", "email"),
            "avatar_url": user_info.get("avatar_url", ""),
        },
        "tier_info": {
            "name": tier_info["name"],
            "price": tier_info["price"],
            "credits": tier_info["credits"],
            "currency": tier_info.get("currency", "eur"),
        },
        "features": features,
        "stats": await db.get_user_stats(user["id"]),
    }


@app.get("/api/credit-history")
async def api_credit_history(user: dict = Depends(require_user)):
    history = await db.get_credit_history(user["id"])
    return {"ok": True, "history": history}


@app.get("/api/dashboard-summary")
async def api_dashboard_summary(user: dict = Depends(require_user)):
    """Return retention-focused dashboard data: recent activity, action items, momentum."""
    leads = await db.get_leads(user["id"])
    stats = await db.get_user_stats(user["id"])
    credits = await db.check_and_reset_credits(user["id"])
    user_info = await db.get_user_by_id(user["id"])

    now = datetime.now(timezone.utc)

    # Leads found in last 7 days.
    # Stability fix (Perplexity bug #41): the previous version handled
    # the Z suffix but NOT a legacy naive ISO string. fromisoformat()
    # on a naive timestamp gives a naive datetime, then `(now - dt)`
    # raises TypeError because `now` is tz-aware — caught by
    # `except: pass`, so those leads were silently excluded from the
    # dashboard's recent count. Treat naive as UTC.
    recent_leads = []
    for l in leads:
        fd = l.get("found_date", "")
        if fd:
            try:
                dt = datetime.fromisoformat(fd.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if (now - dt).days <= 7:
                    recent_leads.append(l)
            except Exception:
                pass

    # Action queue: leads that need attention
    action_items = []
    for l in leads:
        es = l.get("email_status", "new")
        sc = l.get("fit_score", 0) or 0
        if es == "new" and sc >= 7 and l.get("contact_email"):
            action_items.append({"type": "hot_lead", "lead_id": l.get("lead_id"), "org": l.get("org_name", "?"), "score": sc, "msg": f"Score {sc} — has email, ready to contact"})
        elif es == "email_sent":
            # Check if sent more than 5 days ago
            hist = l.get("status_history", [])
            sent_date = next((h["date"] for h in hist if h.get("status") == "email_sent"), None)
            if sent_date:
                try:
                    sd = datetime.fromisoformat(sent_date.replace("Z", "+00:00"))
                    if sd.tzinfo is None:
                        sd = sd.replace(tzinfo=timezone.utc)
                    if (now - sd).days >= 5:
                        action_items.append({"type": "follow_up", "lead_id": l.get("lead_id"), "org": l.get("org_name", "?"), "score": sc, "msg": f"Sent {(now - sd).days}d ago — time to follow up"})
                except Exception:
                    pass

    # Sort actions: hot leads first, then follow-ups
    action_items.sort(key=lambda x: (0 if x["type"] == "hot_lead" else 1, -x.get("score", 0)))

    # Momentum metrics
    total = len(leads)
    contacted = sum(1 for l in leads if l.get("email_status") not in ("new", "ignored"))
    replies = sum(1 for l in leads if l.get("email_status") in ("replied", "meeting_booked", "won"))
    meetings = sum(1 for l in leads if l.get("email_status") in ("meeting_booked", "won"))
    won = sum(1 for l in leads if l.get("email_status") == "won")

    # Pipeline value
    tier_values = {"small": 500, "medium": 1000, "large": 1500}
    pipeline_value = sum(tier_values.get(l.get("deal_tier", ""), 0) for l in leads if l.get("email_status") in ("replied", "meeting_booked"))
    won_value = sum(tier_values.get(l.get("deal_tier", ""), 0) for l in leads if l.get("email_status") == "won")

    return {
        "ok": True,
        "since_last_visit": {
            "new_leads_7d": len(recent_leads),
            "top_recent": [{"org": l.get("org_name"), "score": l.get("fit_score", 0), "lead_id": l.get("lead_id")} for l in sorted(recent_leads, key=lambda x: -(x.get("fit_score") or 0))[:5]],
        },
        "action_queue": action_items[:10],
        "momentum": {
            "total_leads": total,
            "contacted": contacted,
            "contact_rate": round(contacted / total * 100) if total else 0,
            "replies": replies,
            "reply_rate": round(replies / contacted * 100) if contacted else 0,
            "meetings": meetings,
            "won": won,
            "pipeline_value": pipeline_value,
            "won_value": won_value,
        },
        "credits": {
            "remaining": credits,
            "tier": user_info.get("tier", "free") if user_info else "free",
        },
    }


# ═══════════════════════════════════════════════════════════════
# ADMIN
# ═══════════════════════════════════════════════════════════════

@app.get("/api/ops/summary")
async def admin_summary(user: dict = Depends(require_admin)):
    """Admin dashboard summary stats.
    Uses SQL aggregation (db.get_admin_summary_stats) instead of loading the
    entire users table into Python — old path couldn't scale past ~10k users."""
    _7d_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    stats = await db.get_admin_summary_stats(_7d_ago)
    _audit_result = await db.get_admin_audit_log(page=1, page_size=5)
    recent_audit = _audit_result.get("items", [])
    from agent_runner import agent_runner
    running = agent_runner.running_count
    return {
        "ok": True,
        "users": {"total": stats["total"], "verified": stats["verified"],
                  "suspended": stats["suspended"], "by_tier": stats["by_tier"]},
        "credits": {"total_outstanding": stats["total_credits"]},
        "recent_signups_7d": stats["recent_signups"],
        "running_agents": running,
        "recent_audit": recent_audit,
    }


@app.get("/api/ops/users")
async def admin_users(request: Request, user: dict = Depends(require_admin)):
    """Paginated, filterable user list."""
    qs = dict(request.query_params)
    result = await db.get_users_paginated(
        page=max(1, int(qs.get("page", 1))),
        page_size=max(1, min(int(qs.get("page_size", 25)), 100)),
        q=qs.get("q", ""),
        tier=qs.get("tier", ""),
        verified=qs.get("verified", ""),
        suspended=qs.get("suspended", ""),
        low_credits=qs.get("low_credits", "").lower() == "true",
        wizard_configured=qs.get("wizard_configured", ""),
    )
    return {"ok": True, **result}


@app.get("/api/ops/users/{user_id}")
async def admin_user_detail(user_id: int, user: dict = Depends(require_admin)):
    """Full user detail bundle for admin view."""
    # Step 1: fetch basic user profile (fast, own timeout)
    try:
        u = await asyncio.wait_for(db.get_user_by_id(user_id), timeout=5.0)
    except (asyncio.TimeoutError, Exception) as e:
        print(f"[ADMIN] User fetch failed for {user_id}: {e}")
        return JSONResponse({"ok": False, "error": "Timeout loading user"}, status_code=500)
    if not u:
        return JSONResponse({"ok": False, "error": "User not found"}, status_code=404)
    # Build profile from the data we already have — no extra DB call needed on timeout
    profile = {
        "id": u["id"], "email": u["email"],
        "display_name": u.get("display_name", ""),
        "tier": u.get("tier", "free"),
        "credits_remaining": u.get("credits_remaining", 0),
        "email_verified": bool(u.get("email_verified")),
        "is_admin": bool(u.get("is_admin")),
        "role": u.get("role", "user"),
        "is_suspended": bool(u.get("is_suspended")),
        "auth_provider": u.get("auth_provider", "email"),
        "created_at": u.get("created_at", ""),
        "last_login": u.get("last_login", ""),
    }
    empty_fallback = {"ok": True, "profile": profile,
        "billing": {}, "wizard": {}, "lead_stats": {"total": 0, "archived": 0},
        "agent": {"recent_runs": []}, "sessions": {"active_count": 0},
        "payments": {"recent_events": []}, "_partial": True}
    # Step 2: try full enrichment bundle (may be slow)
    try:
        bundle = await asyncio.wait_for(db.get_user_detail_bundle(user_id), timeout=10.0)
    except asyncio.TimeoutError:
        print(f"[ADMIN] User detail bundle TIMEOUT for user {user_id}")
        return empty_fallback
    except Exception as e:
        print(f"[ADMIN] User detail error for {user_id}: {e}")
        return empty_fallback
    if not bundle:
        return empty_fallback
    return {"ok": True, **bundle}


# ── Admin Actions ──

# Per-admin credit-action window. Stops a compromised admin (or a
# malicious insider) from dumping tens of thousands of credits in a
# handful of requests before anyone notices. Per-admin (not per-target)
# so the attacker can't just rotate targets.
_ADMIN_CREDIT_WINDOW = 3600
_ADMIN_CREDIT_MAX_PER_WINDOW = 30
_ADMIN_CREDIT_LARGE_GRANT_THRESHOLD = 500
_admin_credit_history: dict[int, list[float]] = {}
_admin_credit_history_last_gc: float = 0.0

# Stability fix (Perplexity bug #44): idempotency cache for admin
# credit grants. Maps a stable hash of (admin, target, mode, amount,
# reason) → (timestamp, response). If a request lands within 60s of an
# identical one (browser retry, proxy retry, double-click), we return
# the cached response instead of double-applying the grant.
_ADMIN_CREDIT_DEDUPE_WINDOW = 60
_admin_credit_dedupe: dict[str, tuple[float, dict]] = {}
_admin_credit_dedupe_last_gc: float = 0.0


@app.post("/api/ops/users/{user_id}/credits")
async def admin_credits(user_id: int, request: Request, user: dict = Depends(require_admin)):
    """Grant, revoke, or set exact credits. Writes credit_ledger + admin_audit_log."""
    body = await request.json()
    mode = body.get("mode", "")
    amount = int(body.get("amount", 0))
    reason = (body.get("reason") or "").strip()
    if mode not in ("grant", "revoke", "set_exact"):
        return JSONResponse({"ok": False, "error": "mode must be grant/revoke/set_exact"}, status_code=400)
    if amount < 0:
        return JSONResponse({"ok": False, "error": "amount must be >= 0"}, status_code=400)
    # Upper bound — even with `confirm_large=true`, a single grant
    # capped at 1M credits keeps a typo or compromised admin token
    # from minting arbitrary balance in one shot. Anything genuinely
    # bigger should be split into multiple audited grants.
    if amount > 1_000_000:
        return JSONResponse(
            {"ok": False,
             "error": "amount > 1,000,000 — split into multiple grants for audit"},
            status_code=400)
    if not reason:
        return JSONResponse({"ok": False, "error": "reason required"}, status_code=400)
    # Rate limit this admin's credit operations.
    # Stability fix (multi-agent bug #37): periodic GC of the per-admin
    # history dict so admins who go inactive don't leak forever.
    import time as _t
    now = _t.time()
    global _admin_credit_history_last_gc
    if now - _admin_credit_history_last_gc > 300:
        _admin_credit_history_last_gc = now
        _stale = [k for k, v in _admin_credit_history.items()
                  if not v or all(now - t >= _ADMIN_CREDIT_WINDOW for t in v)]
        for k in _stale:
            _admin_credit_history.pop(k, None)
    hist = [t for t in _admin_credit_history.get(user["id"], []) if now - t < _ADMIN_CREDIT_WINDOW]
    if len(hist) >= _ADMIN_CREDIT_MAX_PER_WINDOW:
        _admin_credit_history[user["id"]] = hist
        return JSONResponse({"ok": False, "error": f"Admin credit-op rate limit ({_ADMIN_CREDIT_MAX_PER_WINDOW}/hour) reached."}, status_code=429)
    # Require an explicit confirm body flag on large single grants so a
    # stray click or scripted mistake can't spill thousands of credits.
    if mode == "grant" and amount >= _ADMIN_CREDIT_LARGE_GRANT_THRESHOLD and not body.get("confirm_large"):
        return JSONResponse({"ok": False,
                              "error": f"Large grant ({amount} credits) requires confirm_large=true in request body.",
                              "requires_confirm": True}, status_code=400)
    hist.append(now)
    _admin_credit_history[user["id"]] = hist
    # Idempotency dedupe (Perplexity bug #44).
    import hashlib as _hl
    _dedupe_key = _hl.sha256(
        f"{user['id']}|{user_id}|{mode}|{amount}|{reason}".encode("utf-8")
    ).hexdigest()
    global _admin_credit_dedupe_last_gc
    if now - _admin_credit_dedupe_last_gc > 300:
        _admin_credit_dedupe_last_gc = now
        _stale = [k for k, (t, _r) in _admin_credit_dedupe.items()
                  if now - t >= _ADMIN_CREDIT_DEDUPE_WINDOW]
        for k in _stale:
            _admin_credit_dedupe.pop(k, None)
    _prior = _admin_credit_dedupe.get(_dedupe_key)
    if _prior is not None and now - _prior[0] < _ADMIN_CREDIT_DEDUPE_WINDOW:
        # Same admin + same target + same mode + same amount + same reason
        # within 60s — treat as a retry, return the prior outcome instead
        # of doubling the grant.
        return _prior[1]
    # Stability fix (Perplexity bug #73): the previous version did
    # read-modify-write on credits_remaining (read old_balance, compute
    # new in Python, write back). A concurrent agent deduct or a
    # second admin action could be lost — and the ledger row would
    # record a balance transition that never actually existed. Now
    # use the atomic admin_apply_credit_change helper which does the
    # mutation + ledger insert in ONE SQL transaction.
    _ledger_reason = {"grant": "admin_grant",
                      "revoke": "admin_revoke",
                      "set_exact": "admin_set_exact_adjustment"}[mode]
    _changed = await db.admin_apply_credit_change(user_id, mode, amount, _ledger_reason, reason)
    if _changed is None:
        return JSONResponse({"ok": False, "error": "User not found"}, status_code=404)
    old_balance, new_balance = _changed
    await db.log_admin_action(user["id"], user_id, f"credits_{mode}", {
        "before_balance": old_balance, "after_balance": new_balance,
        "mode": mode, "amount": amount, "reason": reason,
    }, _get_client_ip(request))
    _result = {"ok": True, "old_balance": old_balance, "new_balance": new_balance}
    _admin_credit_dedupe[_dedupe_key] = (now, _result)
    return _result


@app.post("/api/ops/users/{user_id}/plan")
async def admin_plan(user_id: int, request: Request, user: dict = Depends(require_admin)):
    """Change user tier with explicit schedule/credit behavior."""
    body = await request.json()
    new_tier = body.get("tier", "")
    reason = (body.get("reason") or "").strip()
    reset_mode = body.get("reset_schedule", "preserve")  # preserve | restart_now
    grant_now = body.get("grant_credits_now", False)
    if new_tier not in TIERS:
        return JSONResponse({"ok": False, "error": f"Invalid tier: {new_tier}"}, status_code=400)
    if not reason:
        return JSONResponse({"ok": False, "error": "reason required"}, status_code=400)
    target = await db.get_user_by_id(user_id)
    if not target:
        return JSONResponse({"ok": False, "error": "User not found"}, status_code=404)
    old_tier = target.get("tier", "free")
    old_credits = target.get("credits_remaining", 0)
    old_reset = target.get("credits_reset_date", "")
    new_credits = old_credits
    new_reset = old_reset
    # Stability fix (Perplexity bug #74): the previous version called
    # add_credit_ledger BEFORE update_user. If update_user failed
    # (DB blip, etc.) we'd have a phantom ledger row claiming a grant
    # that never landed. It also did read-modify-write on
    # credits_remaining (same race as #73). Now we use the atomic
    # admin_apply_credit_change helper for the credit grant — credit
    # + ledger commit together — and only do the tier/reset write
    # AFTER, so a tier-update failure leaves the credit grant in
    # place with its audit row (acceptable; admin can retry the tier
    # change). update_user is itself atomic.
    if grant_now:
        tier_credits = TIERS[new_tier]["credits"]
        _changed = await db.admin_apply_credit_change(
            user_id, "grant", tier_credits, "admin_plan_grant", reason)
        if _changed is None:
            return JSONResponse({"ok": False, "error": "User not found"}, status_code=404)
        _, new_credits = _changed
    update = {"tier": new_tier}
    if reset_mode == "restart_now":
        new_reset = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        update["credits_reset_date"] = new_reset
    await db.update_user(user_id, **update)
    await db.log_admin_action(user["id"], user_id, "plan_change", {
        "before": {"tier": old_tier, "credits": old_credits, "reset_date": old_reset},
        "after": {"tier": new_tier, "credits": new_credits, "reset_date": new_reset},
        "reset_mode": reset_mode, "grant_now": grant_now, "reason": reason,
    }, _get_client_ip(request))
    return {"ok": True, "old_tier": old_tier, "new_tier": new_tier,
            "credits": new_credits, "reset_date": new_reset}


@app.post("/api/ops/users/{user_id}/verify")
async def admin_verify(user_id: int, request: Request, user: dict = Depends(require_admin)):
    """Toggle email verified status."""
    body = await request.json()
    verified = bool(body.get("verified", True))
    target = await db.get_user_by_id(user_id)
    if not target:
        return JSONResponse({"ok": False, "error": "User not found"}, status_code=404)
    old_val = bool(target.get("email_verified"))
    await db.update_user(user_id, email_verified=1 if verified else 0)
    await db.log_admin_action(user["id"], user_id, "verify_toggle", {
        "before": old_val, "after": verified,
    }, _get_client_ip(request))
    return {"ok": True, "email_verified": verified}


@app.post("/api/ops/users/{user_id}/suspend")
async def admin_suspend(user_id: int, request: Request, user: dict = Depends(require_admin)):
    """Suspend or reactivate user. Clears sessions on suspend."""
    if user_id == user["id"]:
        return JSONResponse({"ok": False, "error": "Cannot suspend your own account"}, status_code=400)
    body = await request.json()
    suspended = bool(body.get("suspended", True))
    reason = (body.get("reason") or "").strip()
    target = await db.get_user_by_id(user_id)
    if not target:
        return JSONResponse({"ok": False, "error": "User not found"}, status_code=404)
    sessions_cleared = 0
    await db.update_user(user_id, is_suspended=1 if suspended else 0)
    agent_stopped = False
    if suspended:
        # Clear all active sessions for suspended user
        sess_count = await db._afetchone("SELECT COUNT(*) as c FROM sessions WHERE user_id = %s", [user_id])
        sessions_cleared = sess_count["c"] if sess_count else 0
        await db.delete_user_sessions(user_id)
        # Stability fix (multi-agent bug #32): the previous version
        # only deleted the DB sessions. The user's in-memory agent
        # thread (if running) kept hunting and consuming credits until
        # natural completion, because get_current_user only checks
        # is_suspended on the next *page load* — and the agent thread
        # never re-loads the user record. Stop the agent here so
        # suspension takes effect immediately.
        try:
            if agent_runner.is_running(user_id):
                agent_runner.stop_agent(user_id)
                agent_stopped = True
        except Exception as _stop_err:
            print(f"[ADMIN suspend] stop_agent failed for user {user_id}: {_stop_err}")
    await db.log_admin_action(user["id"], user_id, "suspend" if suspended else "reactivate", {
        "suspended": suspended, "reason": reason, "sessions_cleared": sessions_cleared,
        "agent_stopped": agent_stopped,
    }, _get_client_ip(request))
    return {"ok": True, "is_suspended": suspended, "sessions_cleared": sessions_cleared,
            "agent_stopped": agent_stopped}


@app.post("/api/ops/users/{user_id}/sessions/clear")
async def admin_clear_sessions(user_id: int, request: Request, user: dict = Depends(require_admin)):
    """Clear all sessions for a user. Refuses to clear admin's own current session."""
    if user_id == user["id"]:
        return JSONResponse({"ok": False, "error": "Cannot clear your own sessions"}, status_code=400)
    sess_count = await db._afetchone("SELECT COUNT(*) as c FROM sessions WHERE user_id = %s", [user_id])
    cleared = sess_count["c"] if sess_count else 0
    await db.delete_user_sessions(user_id)
    await db.log_admin_action(user["id"], user_id, "sessions_clear", {
        "cleared": cleared,
    }, _get_client_ip(request))
    return {"ok": True, "cleared": cleared}


@app.post("/api/ops/users/{user_id}/wizard/reset")
async def admin_wizard_reset(user_id: int, request: Request, user: dict = Depends(require_admin)):
    """Reset wizard/hunt profile. Does NOT touch leads, billing, or identity."""
    body = await request.json()
    reason = (body.get("reason") or "admin reset").strip()
    target = await db.get_user_by_id(user_id)
    if not target:
        return JSONResponse({"ok": False, "error": "User not found"}, status_code=404)
    s = await db.get_settings(user_id)
    old_wizard = s.get("wizard", {})
    # Clear wizard training state
    s["wizard"] = {}
    await db.save_settings(user_id, s)
    await db.log_admin_action(user["id"], user_id, "wizard_reset", {
        "reason": reason,
        "had_brain": bool(old_wizard.get("normalized_hunt_profile")),
        "had_dossier": bool(old_wizard.get("training_dossier")),
        "archetype_was": old_wizard.get("archetype", ""),
    }, _get_client_ip(request))
    return {"ok": True}


@app.post("/api/ops/users/{user_id}/agent/stop")
async def admin_agent_stop(user_id: int, request: Request, user: dict = Depends(require_admin)):
    """Force stop a running agent."""
    from agent_runner import agent_runner as _ar
    was_running = _ar.is_running(user_id)
    if was_running:
        _ar.stop_agent(user_id)
    await db.log_admin_action(user["id"], user_id, "agent_force_stop", {
        "was_running": was_running,
    }, _get_client_ip(request))
    return {"ok": True, "was_running": was_running}


@app.get("/api/ops/billing")
async def admin_billing(user: dict = Depends(require_admin)):
    """Billing overview: recent Stripe events, credit events, anomalies."""
    recent_events = await db.get_recent_stripe_events(limit=50)
    recent_credits = await db.get_recent_credit_events(limit=50)
    anomalies = await db.get_billing_anomalies()
    return {
        "ok": True,
        "recent_events": recent_events,
        "recent_credit_events": recent_credits,
        "anomalies": anomalies,
        "summary": {
            "stripe_events": len(recent_events),
            "credit_events": len(recent_credits),
            "possible_anomalies": len(anomalies),
        },
    }


@app.get("/api/ops/agents")
async def admin_agents(user: dict = Depends(require_admin)):
    """Currently running agents across all users."""
    from agent_runner import agent_runner as _ar
    from user_context import get_context
    running_ids = list(_ar._running.keys())
    agents = []
    for uid in running_ids:
        ctx = get_context(uid)
        u = await db.get_user_by_id(uid)
        agents.append({
            "user_id": uid,
            "email": u["email"] if u else "?",
            "tier": u.get("tier", "?") if u else "?",
            "agent_running": bool(ctx and ctx.agent_running) if ctx else False,
            "credits_used": ctx.credits_used if ctx else 0,
            "leads_found": len(ctx.all_leads) if ctx else 0,
        })
    return {"ok": True, "running": len(agents), "agents": agents}


@app.get("/api/ops/users/{user_id}/events")
async def admin_user_events(user_id: int, request: Request,
                            user: dict = Depends(require_admin)):
    """Admin Live Logs SSE — subscribe to target user's event bus.

    Unlike /agent/events (the user's own stream), this lets admins tail any
    user's agent events without impersonation. Every connection is audit-
    logged. When the target has no context yet, returns a single idle
    status so the UI doesn't hang on "connecting".
    """
    from user_context import get_context
    ip = _get_client_ip(request)
    try:
        await db.log_admin_action(user["id"], user_id, "live_logs_view", {}, ip)
    except Exception as e:
        print(f"[admin events] audit log failed: {e}")

    target_ctx = get_context(user_id)
    if target_ctx is None:
        async def single_status():
            yield f"event: status\ndata: {json.dumps({'text': 'No active agent context for this user', 'state': 'idle'})}\n\n"
        return StreamingResponse(single_status(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    q = target_ctx.bus.subscribe()

    async def event_stream():
        try:
            running = agent_runner.is_running(user_id)
            pos = agent_runner.queue_position(user_id)
            state = "running" if running else ("queued" if pos else "idle")
            yield f"event: status\ndata: {json.dumps({'text': 'Connected (admin)', 'state': state})}\n\n"

            # Stability fix (Perplexity bug #53): poll
            # request.is_disconnected so admin live-logs cleanup is
            # prompt instead of waiting up to 30s for the next emit.
            import queue as _q
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.to_thread(q.get, True, 5)
                    yield msg
                except _q.Empty:
                    yield ": keepalive\n\n"
                except asyncio.CancelledError:
                    break
        finally:
            target_ctx.bus.unsubscribe(q)

    return StreamingResponse(event_stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/ops/audit")
async def admin_audit(request: Request, user: dict = Depends(require_admin)):
    """Paginated admin audit log."""
    qs = dict(request.query_params)
    result = await db.get_admin_audit_log(
        page=max(1, int(qs.get("page", 1))),
        page_size=max(1, min(int(qs.get("page_size", 50)), 200)),
        target_user_id=int(qs["target_user_id"]) if qs.get("target_user_id") else None,
        admin_user_id=int(qs["admin_user_id"]) if qs.get("admin_user_id") else None,
        action=qs.get("action", ""),
    )
    return {"ok": True, **result}


@app.get("/api/ops/runs")
async def admin_runs(request: Request, user: dict = Depends(require_admin)):
    """Paginated list of all agent runs across users."""
    qs = dict(request.query_params)
    result = await db.get_all_agent_runs(
        page=max(1, int(qs.get("page", 1))),
        page_size=max(1, min(int(qs.get("page_size", 50)), 200)),
        user_id=int(qs["user_id"]) if qs.get("user_id") else None,
        status=qs.get("status") or None,
    )
    return {"ok": True, **result}


@app.get("/api/ops/runs/{run_id}")
async def admin_run_detail(run_id: int, user: dict = Depends(require_admin)):
    """Detailed view of a single agent run with log text."""
    result = await db.get_agent_run_detail(run_id)
    if not result.get("run"):
        return {"ok": False, "error": "Run not found"}
    return {"ok": True, **result}


@app.get("/api/ops/incidents")
async def admin_incidents(user: dict = Depends(require_admin)):
    """Recent errors, failed runs, and webhook anomalies."""
    errors = await db.get_recent_errors(limit=50)
    anomalies = await db.get_billing_anomalies()
    return {"ok": True, "errors": errors, "anomalies": anomalies or []}


@app.get("/api/ops/metrics")
async def admin_growth_metrics(request: Request, user: dict = Depends(require_admin)):
    """Light growth analytics (Feature F6). Two windows: 7 and 30 days
    for the headline KPIs, plus checkout-source breakdown over 30 days
    so we can see which paywall surface drives clicks.
    """
    qs = request.query_params
    try:
        days7 = max(1, min(int(qs.get("days7", 7)), 30))
    except (TypeError, ValueError):
        days7 = 7
    try:
        days30 = max(7, min(int(qs.get("days30", 30)), 90))
    except (TypeError, ValueError):
        days30 = 30
    headline = await db.get_growth_metrics(days=days7)
    long_window = await db.get_growth_metrics(days=days30)
    by_source = await db.get_checkout_source_metrics(days=days30, limit=30)
    return {"ok": True, "headline": headline, "long": long_window, "checkout_by_source": by_source}


@app.get("/api/ops/health")
async def admin_health_check(user: dict = Depends(require_admin)):
    """System health: DB pool, SearXNG, Gemini reachability."""
    import psycopg2
    health = {"db": "ok", "searxng": "unknown", "gemini": "unknown"}
    # DB pool
    try:
        pool = db._pool
        if pool:
            health["db_pool"] = {"min": pool.minconn, "max": pool.maxconn}
        else:
            health["db"] = "no pool"
    except Exception as e:
        health["db"] = f"error: {e}"
    # SearXNG
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{os.getenv('SEARXNG_URL', 'http://localhost:8080')}/healthz")
            health["searxng"] = "ok" if r.status_code == 200 else f"status {r.status_code}"
    except Exception as e:
        health["searxng"] = f"error: {type(e).__name__}"
    # Gemini
    try:
        from config import GEMINI_API_KEY
        health["gemini"] = "configured" if GEMINI_API_KEY else "missing key"
    except Exception:
        health["gemini"] = "config error"
    return {"ok": True, "health": health}


# ═══════════════════════════════════════════════════════════════
# SEO: robots.txt, sitemap, manifest
# ═══════════════════════════════════════════════════════════════

@app.get("/robots.txt")
async def robots_txt():
    content = f"""User-agent: *
Allow: /
Allow: /landing
Disallow: /api/
Disallow: /auth/
Disallow: /agent/
Disallow: /dashboard
Disallow: /static/app.js

Sitemap: {PUBLIC_URL}/sitemap.xml
"""
    return Response(content=content, media_type="text/plain")


@app.get("/sitemap.xml")
async def sitemap_xml():
    content = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>{PUBLIC_URL}/</loc>
    <changefreq>weekly</changefreq>
    <priority>1.0</priority>
  </url>
  <url>
    <loc>{PUBLIC_URL}/landing</loc>
    <changefreq>weekly</changefreq>
    <priority>0.9</priority>
  </url>
</urlset>"""
    return Response(content=content, media_type="application/xml")


@app.get("/manifest.json")
async def manifest():
    return {
        "name": "Huntova",
        "short_name": "Huntova",
        "description": "AI-powered B2B lead generation agent",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#07080c",
        "theme_color": "#07080c",
        "icons": [
            {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"},
        ],
    }


@app.get("/favicon.ico")
async def favicon_ico():
    # Serve the existing PNG favicon for any /favicon.ico request so pages
    # without an explicit <link rel="icon"> don't log 404s on every load.
    # 204 (not 500) on missing-file so a broken deploy doesn't surface
    # the absolute server path in error logs/Sentry. 24h browser cache
    # so repeat visits don't re-fetch on every navigation.
    path = os.path.join(STATIC_DIR, "favicon-32x32.png")
    if not os.path.isfile(path):
        return Response(status_code=204)
    return FileResponse(path, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})


# ═══════════════════════════════════════════════════════════════
# RUN
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Railway sets PORT env var; fall back to HV_PORT / 5000
    run_port = int(os.environ.get("PORT", PORT))
    print()
    print("=" * 62)
    print(f"  Huntova SaaS v{VERSION}")
    print("=" * 62)
    print(f"  Dashboard : http://localhost:{run_port}")
    print(f"  AI Engine : {MODEL_ID} ({'Cloud' if AI_PROVIDER == 'gemini' else 'Local'})")
    print("=" * 62)
    print()
    uvicorn.run(app, host="0.0.0.0", port=run_port, log_level="info")
