"""
Huntova SaaS — Database (PostgreSQL via psycopg2)
Connection-pooled PostgreSQL. Thread-safe via ThreadedConnectionPool.
"""
import json
import os
import re
import secrets
from datetime import datetime, timezone, timedelta

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    # psycopg2 is cloud-only. In local SQLite mode the import isn't
    # needed at all. We re-import lazily inside the cloud-only paths.
    psycopg2 = None  # type: ignore[assignment]

from config import TIERS, SESSION_EXPIRY_HOURS

# ── Backend driver (Phase 2 of the local-CLI pivot) ──
# get_driver() picks Postgres (DATABASE_URL set) or SQLite (APP_MODE=local
# without DATABASE_URL) once at startup. db.py keeps using `%s`
# placeholders + RealDictCursor; the driver translates per-query for
# SQLite.
from db_driver import get_driver as _get_driver

DATABASE_URL = os.environ.get("DATABASE_URL", "")
APP_MODE = (os.environ.get("APP_MODE") or "cloud").strip().lower()

# `_pool` stays as the truthy gate the rest of the module already
# checks — but in the local-CLI shape it's the SQLite driver, not a
# psycopg2 pool. The driver presents the same get_conn / put_conn API.
_pool = None
_driver = None
try:
    _driver = _get_driver()
    _pool = _driver  # truthy sentinel for the existing `if _pool:` guards
except RuntimeError as e:
    if os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RENDER"):
        raise RuntimeError(f"FATAL: {e}")
    import sys as _sys
    print(f"[WARNING] {e}", file=_sys.stderr)
except Exception as e:
    import sys as _sys
    print(f"[DB] Failed to init driver: {e}", file=_sys.stderr)


def _is_sqlite() -> bool:
    return bool(_driver and getattr(_driver, "name", None) == "sqlite")


# SQLite singleton-connection serialisation. The local SQLite driver
# (db_driver._SQLiteDriver) returns one shared sqlite3.Connection with
# `check_same_thread=False`. That lets multiple threads access it, but
# concurrent cursor.execute() calls collide with "bad parameter or
# other API misuse". Wrapping every helper that touches the connection
# behind this RLock serialises access.
#
# Cost: SQLite local mode is single-user anyway — concurrent dashboard
# fetches block briefly on this lock. Negligible vs the actual disk I/O.
# Postgres mode is unaffected (the helpers below early-return without
# acquiring the lock).
import threading as _hv_threading
_sqlite_lock = _hv_threading.RLock()


class _SqliteSerial:
    """Context manager that takes _sqlite_lock only in SQLite mode.
    No-op in Postgres mode so the cloud path stays unchanged."""
    def __enter__(self):
        if _is_sqlite():
            _sqlite_lock.acquire()
            self._held = True
        else:
            self._held = False
        return self
    def __exit__(self, *_a):
        if self._held:
            _sqlite_lock.release()


def _cursor(conn):
    """Return a cursor whose fetch results are dict-like.

    Cloud (Postgres): RealDictCursor. Local (SQLite): plain cursor —
    the connection has a row_factory that returns dicts already, so
    the same `row["foo"]` access pattern works in both backends.
    """
    if _is_sqlite():
        return conn.cursor()
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def _xlate(sql: str) -> str:
    """Pass SQL through the driver's translator. No-op in cloud mode."""
    if _driver:
        return _driver.translate_sql(sql)
    return sql


def get_conn():
    if not _driver:
        raise RuntimeError("Database driver not available. Set DATABASE_URL or APP_MODE=local.")
    return _driver.get_conn()


def put_conn(conn):
    """Return a connection to the pool. Sanitize first so a closed OR
    mid-transaction conn doesn't poison the next caller.

    Stability fix (multi-agent bug #14): closed connections are passed
    to putconn(close=True) so a Railway network blip doesn't mark up to
    10 pool slots dead and starve the next 10 callers.

    Stability fix (Perplexity bug #34): even live connections can be
    returned to the pool mid-transaction or in an aborted state.
    psycopg2 starts an implicit transaction on every statement, so any
    code path that skipped the explicit commit/rollback (or saw rollback
    itself fail) hands back a poisoned conn. The next borrower then
    hits "current transaction is aborted, commands ignored…" or gets
    killed by Postgres's idle_in_transaction_session_timeout. Inspect
    transaction_status and rollback if non-IDLE; drop the conn if even
    rollback can't restore IDLE.
    """
    if not (_driver and conn):
        return
    # SQLite: shared singleton connection, the driver's put_conn is a
    # no-op. Skip the psycopg2 introspection entirely.
    if _is_sqlite():
        return
    try:
        # 1) Closed: drop from pool.
        if conn.closed:
            _driver.put_conn(conn)
            return
        # 2) Live but possibly mid-transaction. Defensively rollback if
        # the conn isn't already IDLE.
        try:
            from psycopg2.extensions import (
                TRANSACTION_STATUS_IDLE,
                TRANSACTION_STATUS_UNKNOWN,
            )
            tstatus = conn.get_transaction_status()
            if tstatus == TRANSACTION_STATUS_UNKNOWN:
                _driver.put_conn(conn)
                return
            if tstatus != TRANSACTION_STATUS_IDLE:
                try:
                    conn.rollback()
                except Exception:
                    _driver.put_conn(conn)
                    return
                if conn.get_transaction_status() != TRANSACTION_STATUS_IDLE:
                    _driver.put_conn(conn)
                    return
        except Exception:
            # Couldn't even introspect — treat as bad.
            _driver.put_conn(conn)
            return
        # 3) Healthy + idle: normal return.
        _driver.put_conn(conn)
    except Exception:
        # Pool rejected — close the underlying conn so the fd leak isn't
        # ours.
        try:
            conn.close()
        except Exception:
            pass


def _safe_rollback(conn):
    """Roll back a connection's open transaction without raising.

    Stability fix (multi-agent bug #14): the original code called
    conn.rollback() inside the except block of every helper. If the
    connection is already dead, rollback raises another exception which
    masks the real error the caller is trying to surface (e.g. an
    IntegrityError becomes "connection already closed"). Now a failed
    rollback is swallowed and the original exception bubbles up clean.
    """
    if not conn:
        return
    try:
        conn.rollback()
    except Exception:
        pass


# ── Core helpers ──

def _exec(sql, params=None):
    """Execute statement, return cursor rowcount and lastrowid-like info."""
    with _SqliteSerial():
        conn = get_conn()
        try:
            cur = _cursor(conn)
            cur.execute(_xlate(sql), params)
            conn.commit()
            return cur
        except Exception:
            _safe_rollback(conn)
            raise
        finally:
            put_conn(conn)


def _fetchone(sql, params=None) -> dict | None:
    with _SqliteSerial():
        conn = get_conn()
        try:
            cur = _cursor(conn)
            cur.execute(_xlate(sql), params)
            row = cur.fetchone()
            conn.commit()  # Close implicit transaction — prevents idle-in-transaction
            return dict(row) if row else None
        except Exception:
            _safe_rollback(conn)
            raise
        finally:
            put_conn(conn)


def _fetchall(sql, params=None) -> list:
    with _SqliteSerial():
        conn = get_conn()
        try:
            cur = _cursor(conn)
            cur.execute(_xlate(sql), params)
            rows = cur.fetchall()
            conn.commit()  # Close implicit transaction — prevents idle-in-transaction
            return [dict(r) for r in rows]
        except Exception:
            _safe_rollback(conn)
            raise
        finally:
            put_conn(conn)


def _exec_returning(sql, params=None):
    """Execute INSERT ... RETURNING id and return the id."""
    with _SqliteSerial():
        conn = get_conn()
        try:
            cur = _cursor(conn)
            cur.execute(_xlate(sql), params)
            row = cur.fetchone()
            conn.commit()
            if not row:
                return 0
            # Tolerate either tuple-style (cur.fetchone()[0]) or dict-style
            # (RealDictCursor / sqlite dict factory) returns. Existing
            # callers expect a scalar id.
            if isinstance(row, dict):
                # First column whose name isn't 'was_inserted' — i.e. the id.
                for k, v in row.items():
                    if k != "was_inserted":
                        return v
                # Fallback: any value
                return next(iter(row.values()), 0)
            return row[0]
        except Exception:
            _safe_rollback(conn)
            raise
        finally:
            put_conn(conn)


def _exec_rowcount(sql, params=None) -> int:
    """Execute statement and return number of affected rows."""
    with _SqliteSerial():
        conn = get_conn()
        try:
            cur = _cursor(conn)
            cur.execute(_xlate(sql), params)
            rc = cur.rowcount
            conn.commit()
            return rc
        except Exception:
            _safe_rollback(conn)
            raise
        finally:
            put_conn(conn)


def _exec_pipeline(statements):
    """Execute multiple statements in a single transaction."""
    with _SqliteSerial():
        conn = get_conn()
        try:
            cur = _cursor(conn)
            for sql, params in statements:
                cur.execute(_xlate(sql), params)
            conn.commit()
        except Exception:
            _safe_rollback(conn)
            raise
        finally:
            put_conn(conn)


# ── Async wrappers — run sync DB calls in thread pool ──
import asyncio

async def _aexec(sql, params=None):
    return await asyncio.to_thread(_exec, sql, params)

async def _afetchone(sql, params=None) -> dict | None:
    return await asyncio.to_thread(_fetchone, sql, params)

async def _afetchall(sql, params=None) -> list:
    return await asyncio.to_thread(_fetchall, sql, params)

async def _aexec_returning(sql, params=None):
    return await asyncio.to_thread(_exec_returning, sql, params)

async def _aexec_rowcount(sql, params=None) -> int:
    return await asyncio.to_thread(_exec_rowcount, sql, params)

async def _aexec_pipeline(statements):
    return await asyncio.to_thread(_exec_pipeline, statements)


# ── Schema ──

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL DEFAULT '',
    display_name TEXT DEFAULT '',
    tier TEXT DEFAULT 'free',
    credits_remaining INTEGER DEFAULT 5,
    credits_reset_date TEXT,
    is_admin INTEGER DEFAULT 0,
    role VARCHAR(20) NOT NULL DEFAULT 'user',
    created_at TEXT NOT NULL,
    last_login TEXT,
    google_id TEXT,
    auth_provider TEXT DEFAULT 'email',
    email_verified INTEGER DEFAULT 0,
    avatar_url TEXT DEFAULT '',
    is_suspended INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS leads (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    lead_id TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '{}',
    fit_score INTEGER DEFAULT 0,
    org_name TEXT DEFAULT '',
    country TEXT DEFAULT '',
    email_status TEXT DEFAULT 'new',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_leads_user ON leads(user_id);
CREATE INDEX IF NOT EXISTS idx_leads_lead_id ON leads(lead_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_user_lead ON leads(user_id, lead_id);
CREATE TABLE IF NOT EXISTS archived_leads (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    lead_id TEXT NOT NULL,
    data TEXT NOT NULL DEFAULT '{}',
    archived_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_settings (
    user_id INTEGER PRIMARY KEY,
    data TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS seen_history (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    url_hash TEXT NOT NULL,
    UNIQUE(user_id, url_hash)
);
CREATE TABLE IF NOT EXISTS seen_fingerprints (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    fingerprint TEXT NOT NULL,
    UNIQUE(user_id, fingerprint)
);
CREATE TABLE IF NOT EXISTS domain_blocklist (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    domain TEXT NOT NULL,
    fail_count INTEGER DEFAULT 1,
    UNIQUE(user_id, domain)
);
CREATE TABLE IF NOT EXISTS user_blocked (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    block_type TEXT NOT NULL,
    value TEXT NOT NULL,
    UNIQUE(user_id, block_type, value)
);
CREATE TABLE IF NOT EXISTS agent_runs (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    status TEXT DEFAULT 'running',
    leads_found INTEGER DEFAULT 0,
    ai_calls INTEGER DEFAULT 0,
    queries_total INTEGER DEFAULT 0,
    queries_done INTEGER DEFAULT 0,
    started_at TEXT NOT NULL,
    ended_at TEXT
);
CREATE TABLE IF NOT EXISTS stripe_events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT,
    user_id INTEGER,
    product_id TEXT,
    processed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS credit_ledger (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    amount INTEGER NOT NULL,
    balance_after INTEGER NOT NULL,
    reason TEXT NOT NULL,
    reference TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_credit_ledger_user ON credit_ledger(user_id);
CREATE TABLE IF NOT EXISTS admin_audit_log (
    id SERIAL PRIMARY KEY,
    admin_user_id INTEGER NOT NULL,
    target_user_id INTEGER,
    action TEXT NOT NULL,
    details TEXT DEFAULT '{}',
    ip TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_admin ON admin_audit_log(admin_user_id);
CREATE INDEX IF NOT EXISTS idx_audit_target ON admin_audit_log(target_user_id);
CREATE INDEX IF NOT EXISTS idx_audit_created ON admin_audit_log(created_at);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_archived_user ON archived_leads(user_id);
CREATE INDEX IF NOT EXISTS idx_archived_user_lead ON archived_leads(user_id, lead_id);
CREATE INDEX IF NOT EXISTS idx_stripe_user ON stripe_events(user_id);
CREATE INDEX IF NOT EXISTS idx_agent_runs_user ON agent_runs(user_id);
CREATE INDEX IF NOT EXISTS idx_users_google ON users(google_id);
CREATE TABLE IF NOT EXISTS lead_feedback (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    lead_id TEXT NOT NULL,
    signal TEXT NOT NULL,
    reason TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feedback_user ON lead_feedback(user_id);
CREATE INDEX IF NOT EXISTS idx_feedback_user_signal_created ON lead_feedback(user_id, signal, created_at DESC);
-- NOTE: idx_lead_feedback_user_lead (UNIQUE) used to be declared here
-- so SQLite installs would inherit it. Stability fix (audit wave 27):
-- on Postgres installs that pre-date Perplexity bug #77's dedupe (i.e.
-- the very installs the dedupe was written to rescue), running this
-- statement inside SCHEMA_SQL would fail with "could not create
-- unique index … is duplicated" — and the post-SCHEMA dedupe block
-- at init_db_sync line ~686 would never run because the whole SCHEMA
-- transaction had rolled back. Schema became un-initializable on the
-- exact installs the dedupe was meant to fix. The unique index is
-- now created post-dedupe in init_db_sync (Postgres) and via a
-- separate idempotent CREATE UNIQUE INDEX called explicitly for
-- SQLite (init_db_sync below) — see "Post-dedupe unique index"
-- block.
CREATE TABLE IF NOT EXISTS agent_run_logs (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    run_id INTEGER,
    log_text TEXT NOT NULL DEFAULT '',
    leads_found INTEGER DEFAULT 0,
    queries_run INTEGER DEFAULT 0,
    urls_checked INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_run_logs_user ON agent_run_logs(user_id);
CREATE TABLE IF NOT EXISTS agent_dna (
    user_id INTEGER PRIMARY KEY,
    dna_json TEXT NOT NULL DEFAULT '{}',
    version INTEGER DEFAULT 1,
    generated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lead_actions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    lead_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    score_band TEXT DEFAULT '',
    confidence_band TEXT DEFAULT '',
    meta TEXT DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lead_actions_user ON lead_actions(user_id);
CREATE INDEX IF NOT EXISTS idx_lead_actions_type ON lead_actions(action_type);
CREATE TABLE IF NOT EXISTS user_learning_profile (
    user_id INTEGER PRIMARY KEY,
    preferences TEXT NOT NULL DEFAULT '{}',
    instruction_summary TEXT NOT NULL DEFAULT '',
    signals_processed INTEGER DEFAULT 0,
    version INTEGER DEFAULT 1,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS used_reset_tokens (
    token_hash TEXT PRIMARY KEY,
    used_at TEXT NOT NULL
);
-- Feature F1 (Perplexity round 59): shareable replay URLs.
-- A `slug` resolves to a frozen-in-time snapshot of one or more leads
-- the creator wants to surface publicly (sales asset, founder
-- outreach, landing-page proof). Snapshot, NOT live-link, so the
-- shared page stays stable when the creator later edits notes or
-- statuses in their CRM.
CREATE TABLE IF NOT EXISTS hunt_shares (
    slug TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    run_id INTEGER,
    snapshot TEXT NOT NULL DEFAULT '{}',
    title TEXT DEFAULT '',
    revoked INTEGER DEFAULT 0,
    view_count INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    expires_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_hunt_shares_user ON hunt_shares(user_id);
-- Feature: hunt recipes (round-67 brainstorm — Tab 0's "Re-run + Diff").
-- A recipe is a named, replayable hunt config. Users save the
-- parameters of a successful hunt under a memorable name, then
-- `huntova recipe run <name>` replays the same hunt. Future
-- iterations compute a diff vs prior leads to show what changed.
CREATE TABLE IF NOT EXISTS hunt_recipes (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    config_json TEXT NOT NULL DEFAULT '{}',
    description TEXT DEFAULT '',
    last_run_at TEXT,
    last_run_lead_ids TEXT NOT NULL DEFAULT '[]',
    adaptation_json TEXT NOT NULL DEFAULT '{}',
    adaptation_at TEXT,
    run_count INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(user_id, name)
);
CREATE INDEX IF NOT EXISTS idx_hunt_recipes_user ON hunt_recipes(user_id);
-- Feature F6 (Perplexity round 64): light growth analytics. Capture
-- intent at checkout-start (which paywall surface drove the click)
-- separately from completed payments — completed-only data hides the
-- best signal: which prompts converted vs which only got clicks.
CREATE TABLE IF NOT EXISTS checkout_starts (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL,
    product_id TEXT NOT NULL,
    source TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_checkout_starts_created ON checkout_starts(created_at);

-- Opt-in launch metrics (Kimi round-72 spec). Three event types
-- only: try_submit (server-side on /api/try success), cli_init (CLI
-- after `huntova init`), cli_hunt (CLI after a hunt completes).
-- props is a JSON blob of small numeric / boolean / short-string
-- fields. No keys, no queries, no PII land here.
CREATE TABLE IF NOT EXISTS metrics (
    id SERIAL PRIMARY KEY,
    event TEXT NOT NULL,
    platform TEXT DEFAULT '',
    version TEXT DEFAULT '',
    props TEXT DEFAULT '',
    ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_metrics_event_ts ON metrics(event, ts);
CREATE INDEX IF NOT EXISTS idx_metrics_ts ON metrics(ts);

-- Public recipe registry (Kimi round-74 spec — gated by
-- HV_RECIPE_URL_BETA at the route layer pre-launch). Lets users publish
-- a tuned recipe at /r/<slug> and import it on another machine via
-- `huntova recipe import-url`. Recipes are JSON blobs with the same
-- shape as hunt_recipes.recipe_payload but globally addressable.
CREATE TABLE IF NOT EXISTS public_recipes (
    id SERIAL PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    payload TEXT NOT NULL,
    name TEXT DEFAULT '',
    description TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_public_recipes_slug ON public_recipes(slug);
CREATE INDEX IF NOT EXISTS idx_public_recipes_created ON public_recipes(created_at);

-- Cloud Proxy tokens (GPT round-76 paid wedge MVP). Each row = one
-- per-user API token for the managed Huntova Cloud Search endpoint.
-- Users drop the token into HV_SEARXNG_URL=https://huntova.com/cloud-search/<token>
-- and the local CLI works unchanged.
CREATE TABLE IF NOT EXISTS cloud_proxy_tokens (
    id SERIAL PRIMARY KEY,
    token TEXT NOT NULL UNIQUE,
    user_email TEXT DEFAULT '',
    plan TEXT DEFAULT 'design_partner',
    daily_quota INTEGER DEFAULT 200,
    used_today INTEGER DEFAULT 0,
    last_reset_date TEXT DEFAULT '',
    expires_at TEXT,
    created_at TEXT NOT NULL,
    revoked_at TEXT,
    notes TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_cloud_tokens_token ON cloud_proxy_tokens(token);
CREATE INDEX IF NOT EXISTS idx_cloud_tokens_email ON cloud_proxy_tokens(user_email);

-- Share view tracker (Kimi round-76 week-4 ship). One row per /h/<slug>
-- page view (de-duped per IP-hash + slug per hour). Lets share owners
-- see "23 people viewed your shared hunt" — engagement signal that
-- creates a retention hook ("someone's looking, follow up!").
CREATE TABLE IF NOT EXISTS share_views (
    id SERIAL PRIMARY KEY,
    slug TEXT NOT NULL,
    ip_hash TEXT DEFAULT '',
    user_agent TEXT DEFAULT '',
    referrer TEXT DEFAULT '',
    viewed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_share_views_slug ON share_views(slug);
CREATE INDEX IF NOT EXISTS idx_share_views_when ON share_views(viewed_at);
"""


def init_db_sync():
    """Create all tables. Safe to run multiple times."""
    # SQLite path: let the driver translate + executescript the whole
    # SCHEMA_SQL bundle. Skip the cloud-era migrations — those are
    # one-shot fixups against legacy Postgres rows that don't exist on
    # a fresh local install.
    if _is_sqlite():
        _driver.init_schema(SCHEMA_SQL)
        # Post-dedupe unique index (audit wave 27): the unique index
        # used to live inside SCHEMA_SQL but was relocated here so the
        # Postgres path can dedupe FIRST (legacy installs predating
        # Perplexity bug #77 have duplicate lead_feedback rows that
        # would otherwise crash the unique-index creation inside
        # SCHEMA_SQL and roll back the whole transaction). Fresh SQLite
        # installs have no duplicates by construction, so no dedupe
        # step needed — just create the unique index.
        try:
            _driver.init_schema(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "idx_lead_feedback_user_lead ON lead_feedback(user_id, lead_id);"
            )
        except Exception:
            pass
        # Only emit the init log on first-run or when HV_VERBOSE_LOGS=1.
        # Lightweight CLI commands (status / config / etc.) re-import db
        # but don't actually create the schema; we shouldn't pollute
        # their output.
        if os.environ.get("HV_VERBOSE_LOGS"):
            import sys as _sys
            print("[DB] Schema initialized (SQLite)", file=_sys.stderr)
        return
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(SCHEMA_SQL)
        conn.commit()
        # ── Migrations ──
        # Add role column (Phase 1 Track A: Admin Separation)
        try:
            cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(20) NOT NULL DEFAULT 'user'")
            conn.commit()
        except Exception:
            conn.rollback()
        # Add reason column to lead_feedback
        try:
            cur.execute("ALTER TABLE lead_feedback ADD COLUMN IF NOT EXISTS reason TEXT DEFAULT ''")
            conn.commit()
        except Exception:
            conn.rollback()
        # Back-fill role from legacy is_admin flag
        try:
            cur.execute("UPDATE users SET role = 'superadmin' WHERE is_admin = 1 AND (role IS NULL OR role = 'user')")
            conn.commit()
        except Exception:
            conn.rollback()
        # Stability fix (multi-agent bug #28): composite indexes on hot
        # paginated queries. Existing single-column user_id indexes filter
        # the row set, but the ORDER BY created_at DESC then forced a sort
        # over all of that user's rows. With these composite indexes the
        # planner can serve the page directly from index order.
        try:
            cur.execute("CREATE INDEX IF NOT EXISTS idx_leads_user_created ON leads(user_id, created_at DESC)")
            conn.commit()
        except Exception:
            conn.rollback()
        try:
            cur.execute("CREATE INDEX IF NOT EXISTS idx_run_logs_user_created ON agent_run_logs(user_id, created_at DESC)")
            conn.commit()
        except Exception:
            conn.rollback()
        # Stability fix (Perplexity bug #77): dedupe lead_feedback (keep
        # latest per user+lead) then enforce unique constraint so
        # double-clicks can't inflate DNA-regen counts. The DELETE is
        # a no-op once the index exists; CREATE UNIQUE INDEX IF NOT
        # EXISTS is idempotent so this whole block is safe to re-run.
        try:
            cur.execute(
                "DELETE FROM lead_feedback a USING lead_feedback b "
                "WHERE a.id < b.id AND a.user_id = b.user_id AND a.lead_id = b.lead_id")
            conn.commit()
        except Exception:
            conn.rollback()
        try:
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_lead_feedback_user_lead "
                "ON lead_feedback(user_id, lead_id)")
            conn.commit()
        except Exception:
            conn.rollback()
        if os.environ.get("HV_VERBOSE_LOGS"):
            import sys as _sys
            print("[DB] Schema initialized (PostgreSQL)", file=_sys.stderr)
    except Exception as e:
        _safe_rollback(conn)
        import sys as _sys
        print(f"[DB] Schema init error: {e}", file=_sys.stderr)
        raise
    finally:
        put_conn(conn)


async def init_db():
    await asyncio.to_thread(init_db_sync)


# ── Users ──

async def create_user(email: str, password_hash: str, display_name: str = "") -> int:
    now = datetime.now(timezone.utc).isoformat()
    reset = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    free_credits = TIERS.get("free", {}).get("credits", 5)
    return await _aexec_returning(
        "INSERT INTO users (email, password_hash, display_name, tier, credits_remaining, credits_reset_date, created_at) "
        "VALUES (%s, %s, %s, 'free', %s, %s, %s) RETURNING id",
        [email.lower().strip(), password_hash, display_name, free_credits, reset, now]
    )


async def get_user_by_email(email: str) -> dict | None:
    # Stability fix (Perplexity bug #54): use LOWER(email) on the
    # column side so a row stored mixed-case (legacy import, manual
    # admin insert, future code that forgets to normalize) is still
    # findable. All write paths today already lowercase before INSERT
    # so this is defense in depth — no current row should be
    # affected, and no plan change is needed in the calling sites.
    return await _afetchone("SELECT * FROM users WHERE LOWER(email) = %s", [email.lower().strip()])


async def get_user_by_id(user_id: int) -> dict | None:
    return await _afetchone("SELECT * FROM users WHERE id = %s", [user_id])


async def update_user(user_id: int, **fields):
    if not fields:
        return
    sets = ", ".join(f"{k} = %s" for k in fields)
    vals = list(fields.values()) + [user_id]
    await _aexec(f"UPDATE users SET {sets} WHERE id = %s", vals)


async def update_last_login(user_id: int):
    await update_user(user_id, last_login=datetime.now(timezone.utc).isoformat())


async def check_and_reset_credits(user_id: int) -> int:
    """Refill monthly credits if the reset_date has passed.

    Stability fix (multi-agent bug #17): the previous implementation
    was vulnerable to a TOCTOU double-refill race. /auth/me runs this
    on every page load, so a user with two tabs open would fire two
    concurrent calls; both saw the past reset_date, both wrote
    credits_remaining + refill, and the user got double the monthly
    credits for free. Now we use an atomic compare-and-swap on
    credits_reset_date — only the first writer succeeds, the loser
    re-reads the freshly-refilled balance.
    """
    user = await get_user_by_id(user_id)
    if not user:
        return 0
    reset_date = user.get("credits_reset_date")
    if not reset_date:
        return user.get("credits_remaining", 0)
    try:
        _rd = datetime.fromisoformat(str(reset_date))
    except (ValueError, TypeError):
        return user.get("credits_remaining", 0)
    if not _rd.tzinfo:
        _rd = _rd.replace(tzinfo=timezone.utc)
    if _rd > datetime.now(timezone.utc):
        return user.get("credits_remaining", 0)

    # Reset date is past — handle webhook-deferred renewals first.
    # Stability fix (Perplexity bug #45): credit_ledger.created_at is
    # TEXT, so the previous WHERE created_at > %s did a STRING compare,
    # not a real timestamp compare. ISO-8601 strings sort
    # chronologically only when every row uses the same suffix; if any
    # legacy row was written with "Z" while threshold uses "+00:00"
    # (or vice versa), the row could fall on the wrong side of the
    # window — leading to either a missed dedup (double refill) or a
    # missed refill. Fetch the latest renewal and parse it in Python so
    # we compare actual datetimes.
    recent_row = await _afetchone(
        "SELECT created_at FROM credit_ledger "
        "WHERE user_id = %s AND reason = 'subscription_renewal' "
        "ORDER BY id DESC LIMIT 1",
        [user_id])
    _recent_within_window = False
    if recent_row and recent_row.get("created_at"):
        try:
            _created = datetime.fromisoformat(str(recent_row["created_at"]).replace("Z", "+00:00"))
            if not _created.tzinfo:
                _created = _created.replace(tzinfo=timezone.utc)
            if _created > datetime.now(timezone.utc) - timedelta(days=25):
                _recent_within_window = True
        except (ValueError, TypeError):
            _recent_within_window = False
    if _recent_within_window:
        # Webhook already credited this cycle — just bump the reset_date
        # forward atomically. CAS still applies so two callers don't both
        # roll the date forward (harmless but wasteful).
        new_reset = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        await _aexec_rowcount(
            "UPDATE users SET credits_reset_date = %s WHERE id = %s AND credits_reset_date = %s",
            [new_reset, user_id, str(reset_date)])
        # Read fresh balance instead of returning the stale snapshot
        # from before the date bump (the date bump itself doesn't
        # change credits, but a concurrent topup could have).
        fresh = await get_user_by_id(user_id)
        return (fresh or {}).get("credits_remaining", 0) or 0

    tier_info = TIERS.get(user["tier"], TIERS["free"])
    refill_amount = tier_info["credits"]
    if refill_amount <= 0:
        return user.get("credits_remaining", 0)

    new_reset = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    # CAS: refill only if the reset_date row we read is still the one
    # in the DB. A concurrent caller that beats us flips reset_date
    # forward; our update then matches 0 rows and we re-read.
    rows = await _aexec_rowcount(
        "UPDATE users SET credits_remaining = credits_remaining + %s, credits_reset_date = %s "
        "WHERE id = %s AND credits_reset_date = %s",
        [refill_amount, new_reset, user_id, str(reset_date)])
    if rows == 0:
        # Another caller refilled first — return their post-refill balance.
        fresh = await get_user_by_id(user_id)
        return (fresh or {}).get("credits_remaining", 0) or 0

    # We won — log the ledger entry. Read fresh balance instead of
    # current+refill in case of concurrent topups (extra safety).
    fresh = await get_user_by_id(user_id)
    new_credits = (fresh or {}).get("credits_remaining", 0) or 0
    await add_credit_ledger(user_id, refill_amount, new_credits, "monthly_refill", f"tier:{user['tier']}")
    return new_credits


async def deduct_credit(user_id: int, amount: int = 1) -> bool:
    # Stability fix (Perplexity bug #46): reject negative/zero amount
    # so a buggy caller can't invert the money direction.
    # Stability fix (Perplexity bug #55): the credits-decrement and the
    # ledger insert now happen in ONE transaction via apply_credit_delta
    # — a crash between the two no longer leaves the user's balance
    # changed without an audit row.
    if amount <= 0:
        raise ValueError("deduct_credit amount must be > 0")
    credits = await check_and_reset_credits(user_id)
    if credits < amount:
        return False
    applied, _balance = await apply_credit_delta(
        user_id, -amount, "lead_found", "",
        gate="credits_remaining >= %s")
    return applied


async def refund_credit(user_id: int, amount: int, reason: str, reference: str = ""):
    # Return credits after a pre-paid op (e.g. Deep Research) failed.
    # Stability fix (Perplexity bug #46): reject inverted amount.
    # Stability fix (Perplexity bug #55): credit + ledger atomic.
    if amount <= 0:
        raise ValueError("refund_credit amount must be > 0")
    await apply_credit_delta(user_id, amount, reason, reference)


def _claim_reset_token_and_set_password_sync(token_hash: str, user_id: int, new_password_hash: str) -> bool:
    """Atomically claim a reset token and update the password + wipe sessions.

    Stability fix (Perplexity bug #58): the previous flow called
    mark_reset_token_used (own connection) FIRST, then update_user
    (separate connection) and delete_user_sessions (third connection).
    Any failure after the token claim left the token burned but the
    password unchanged — the user couldn't retry. Now all three
    happen inside one transaction; if any step raises the
    transaction rolls back and the token is still usable.

    Returns True on success, False if the token was already used.
    """
    if not _pool:
        raise RuntimeError("Database connection pool not available")
    now = datetime.now(timezone.utc).isoformat()
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO used_reset_tokens (token_hash, used_at) "
            "VALUES (%s, %s) ON CONFLICT (token_hash) DO NOTHING",
            [token_hash, now])
        if cur.rowcount == 0:
            conn.rollback()
            return False
        cur.execute(
            "UPDATE users SET password_hash = %s WHERE id = %s",
            [new_password_hash, user_id])
        cur.execute(
            "DELETE FROM sessions WHERE user_id = %s",
            [user_id])
        conn.commit()
        return True
    except Exception:
        _safe_rollback(conn)
        raise
    finally:
        put_conn(conn)


async def claim_reset_token_and_set_password(token_hash: str, user_id: int, new_password_hash: str) -> bool:
    return await asyncio.to_thread(_claim_reset_token_and_set_password_sync, token_hash, user_id, new_password_hash)


async def mark_reset_token_used(token_hash: str) -> bool:
    # Returns True if this call actually inserted the row (first use),
    # False if the row was already present (token already used).
    # Callers gate the password write on the return value so a single
    # leaked token can't be used twice inside its signed expiry window.
    sql = ("INSERT INTO used_reset_tokens (token_hash, used_at) "
           "VALUES (%s, %s) ON CONFLICT (token_hash) DO NOTHING")
    now = datetime.now(timezone.utc).isoformat()
    # Stability fix (round-3 multi-agent): never silently treat a DB error
    # as "token already used" — that traps legitimate users with a confusing
    # "Reset token invalid" forever. Bubble up so the route returns 5xx and
    # the user can retry.
    rows = await _aexec_rowcount(sql, [token_hash, now])
    return rows > 0


async def cleanup_stale_token_tables():
    # Prune rows we no longer need: used_reset_tokens (after the 1h signed
    # window expires the hash can't be used anyway) and stripe_events
    # (after ~30 days Stripe won't redeliver the same event id). Keeps the
    # tables from growing unbounded.
    day_ago = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    month_ago = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    try:
        await _aexec("DELETE FROM used_reset_tokens WHERE used_at < %s", [day_ago])
    except Exception:
        pass
    try:
        await _aexec("DELETE FROM stripe_events WHERE processed_at < %s", [month_ago])
    except Exception:
        pass


async def check_webhook_processed(event_id: str) -> bool:
    row = await _afetchone("SELECT event_id FROM stripe_events WHERE event_id = %s", [event_id])
    return row is not None


async def rollback_webhook(event_id: str):
    """Delete a recorded webhook row so a Stripe retry can re-process.

    Stability fix (Perplexity bug #52): record_webhook claims the
    event_id BEFORE side effects run. If side effects crash midway,
    the next retry sees the recorded row and short-circuits — the
    user permanently misses credits. Callers should call this from an
    exception path before re-raising so the retry can succeed.
    """
    await _aexec("DELETE FROM stripe_events WHERE event_id = %s", [event_id])


async def record_webhook(event_id: str, event_type: str, user_id: int, product_id: str) -> bool:
    # Returns True if this call actually inserted the row (first-writer wins)
    # and False if the row was already present (ON CONFLICT). Callers gate
    # side-effecting mutations on the return value to make the idempotency
    # check-and-mutate pair race-free: two concurrent webhooks for the same
    # event can both pass check_webhook_processed, but only one will see
    # True from this insert and do the credit update.
    # Stability fix (round-3 multi-agent + round-4 Perplexity): never swallow
    # DB exceptions here. True = first-writer (proceed), False = duplicate
    # (skip side effects, still 200), exception = infra failure → bubbles up
    # so FastAPI returns 5xx and Stripe retries the webhook.
    now = datetime.now(timezone.utc).isoformat()
    rows = await _aexec_rowcount(
        "INSERT INTO stripe_events (event_id, event_type, user_id, product_id, processed_at) "
        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (event_id) DO NOTHING",
        [event_id, event_type, user_id, product_id, now])
    return rows > 0


async def add_credit_ledger(user_id: int, amount: int, balance_after: int, reason: str, reference: str = ""):
    now = datetime.now(timezone.utc).isoformat()
    await _aexec(
        "INSERT INTO credit_ledger (user_id, amount, balance_after, reason, reference, created_at) VALUES (%s, %s, %s, %s, %s, %s)",
        [user_id, amount, balance_after, reason, reference, now])


def _apply_credit_delta_sync(user_id: int, delta: int, reason: str, reference: str,
                              gate: str = "") -> tuple[bool, int]:
    """Atomic credits_remaining adjustment + ledger insert in ONE transaction.

    Stability fix (Perplexity bug #55): the legacy pattern was
    update_user → add_credit_ledger as two separate `_aexec` calls,
    each on its own pooled connection. A process crash or DB blip
    between the two left credits_remaining changed but no audit row
    (or vice versa). Now both happen inside a single transaction so
    they commit together or roll back together.

    Returns (applied, new_balance):
      - applied=True with new_balance: delta was applied
      - applied=False with current_balance: WHERE-gate failed
        (insufficient credits for a deduction); nothing written
    """
    if not _pool:
        raise RuntimeError("Database connection pool not available")
    now = datetime.now(timezone.utc).isoformat()
    conn = get_conn()
    try:
        # Driver-agnostic cursor — same reasoning as the admin helper
        # fix in a46. Hardcoded RealDictCursor breaks SQLite local mode
        # if this credit-delta path ever fires there (cloud-only today,
        # but the abstraction stays consistent).
        cur = _cursor(conn)
        # Apply delta with optional WHERE gate (e.g. "credits_remaining >= %s"
        # for atomic deductions). gate is a parameterised SQL fragment that
        # references the same %s-positional `delta` value.
        if gate:
            cur.execute(
                "UPDATE users SET credits_remaining = credits_remaining + %s "
                "WHERE id = %s AND " + gate + " "
                "RETURNING credits_remaining",
                [delta, user_id, abs(delta)])
        else:
            cur.execute(
                "UPDATE users SET credits_remaining = credits_remaining + %s "
                "WHERE id = %s "
                "RETURNING credits_remaining",
                [delta, user_id])
        row = cur.fetchone()
        if row is None:
            # WHERE-gate failed — fetch current for caller signal.
            conn.rollback()
            cur2 = _cursor(conn)
            cur2.execute("SELECT credits_remaining FROM users WHERE id = %s", [user_id])
            now_row = cur2.fetchone()
            return (False, (now_row["credits_remaining"] if now_row else 0))
        new_balance = row["credits_remaining"]
        cur.execute(
            "INSERT INTO credit_ledger (user_id, amount, balance_after, reason, reference, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            [user_id, delta, new_balance, reason, reference, now])
        conn.commit()
        return (True, new_balance)
    except Exception:
        _safe_rollback(conn)
        raise
    finally:
        put_conn(conn)


async def apply_credit_delta(user_id: int, delta: int, reason: str, reference: str = "",
                              gate: str = "") -> tuple[bool, int]:
    """Async wrapper around the atomic credit+ledger helper. See
    _apply_credit_delta_sync for semantics."""
    return await asyncio.to_thread(_apply_credit_delta_sync, user_id, delta, reason, reference, gate)


def _admin_apply_credit_change_sync(user_id: int, mode: str, amount: int,
                                     ledger_reason: str, reference: str) -> tuple[int, int] | None:
    """Atomic admin credit mutation across grant / revoke / set_exact.

    Stability fix (Perplexity bug #73): the admin route used to do
    read-modify-write on credits_remaining. Concurrent agent deducts
    or a second admin action could be lost. Now one SQL mutation
    returns (old_balance, new_balance); the ledger gets a row with
    the actual delta computed by Postgres, not a Python snapshot.

    Returns (old_balance, new_balance) or None if user not found.
    """
    if not _pool:
        raise RuntimeError("Database connection pool not available")
    conn = get_conn()
    try:
        # Use the driver-agnostic cursor wrapper instead of hardcoding
        # psycopg2.extras.RealDictCursor — keeps the cloud behavior
        # identical while keeping the SQLite path syntactically valid
        # if this admin function is ever wired up in local mode.
        cur = _cursor(conn)
        if mode == "grant":
            cur.execute(
                "UPDATE users SET credits_remaining = credits_remaining + %s "
                "WHERE id = %s "
                "RETURNING credits_remaining - %s AS old_balance, credits_remaining AS new_balance",
                [amount, user_id, amount])
        elif mode == "revoke":
            cur.execute(
                "UPDATE users SET credits_remaining = GREATEST(0, credits_remaining - %s) "
                "WHERE id = %s "
                "RETURNING "
                "(SELECT credits_remaining FROM users WHERE id = %s) + %s AS new_balance_check, "
                "credits_remaining AS new_balance",
                [amount, user_id, user_id, 0])
            # Postgres doesn't let us reference the OLD row easily here.
            # Re-issue a small CTE-style query if needed; simpler: use
            # the row count and compute old=new+delta in caller. Instead,
            # use a compound CTE so we get both values cleanly.
        else:  # set_exact
            # Drop the Postgres-only `::int` cast on `%s` — `amount` is
            # already an int from the body validation above and the cast
            # blocks SQLite from parsing the statement at all.
            cur.execute(
                "WITH old AS (SELECT credits_remaining FROM users WHERE id = %s) "
                "UPDATE users SET credits_remaining = %s "
                "WHERE id = %s "
                "RETURNING (SELECT credits_remaining FROM old) AS old_balance, %s AS new_balance",
                [user_id, amount, user_id, amount])
        row = cur.fetchone()
        if row is None:
            conn.rollback()
            return None
        # For revoke we did a partial query above; re-resolve with one
        # more SQL using a CTE so old_balance is captured atomically
        # in the same row that does the update. Replace the revoke
        # branch with the CTE form here for correctness.
        if mode == "revoke":
            # Roll back the partial revoke and re-do via CTE so we
            # have both old + new in one returning row.
            conn.rollback()
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(
                "WITH before AS (SELECT credits_remaining AS old FROM users WHERE id = %s) "
                "UPDATE users SET credits_remaining = GREATEST(0, credits_remaining - %s) "
                "WHERE id = %s "
                "RETURNING (SELECT old FROM before) AS old_balance, credits_remaining AS new_balance",
                [user_id, amount, user_id])
            row = cur.fetchone()
            if row is None:
                conn.rollback()
                return None
        old_balance = int(row["old_balance"]) if row.get("old_balance") is not None else 0
        new_balance = int(row["new_balance"])
        # Ledger row using the Postgres-computed delta — not a Python
        # snapshot that may have been stale.
        ledger_amount = new_balance - old_balance
        cur.execute(
            "INSERT INTO credit_ledger (user_id, amount, balance_after, reason, reference, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            [user_id, ledger_amount, new_balance, ledger_reason, reference,
             datetime.now(timezone.utc).isoformat()])
        conn.commit()
        return (old_balance, new_balance)
    except Exception:
        _safe_rollback(conn)
        raise
    finally:
        put_conn(conn)


async def admin_apply_credit_change(user_id: int, mode: str, amount: int,
                                     ledger_reason: str, reference: str) -> tuple[int, int] | None:
    return await asyncio.to_thread(_admin_apply_credit_change_sync, user_id, mode, amount, ledger_reason, reference)


async def get_credit_history(user_id: int, limit: int = 50) -> list:
    return await _afetchall(
        "SELECT amount, balance_after, reason, reference, created_at FROM credit_ledger WHERE user_id = %s ORDER BY created_at DESC LIMIT %s",
        [user_id, limit])


async def get_all_users() -> list:
    return await _afetchall(
        "SELECT id, email, display_name, tier, credits_remaining, is_admin, role, email_verified, is_suspended, created_at, last_login FROM users ORDER BY created_at DESC")


async def get_admin_summary_stats(since_iso: str) -> dict:
    # One-shot aggregation replacing the previous 'load the full users table
    # into Python and count in a loop' pattern in admin_summary. Scales to
    # tens of thousands of rows without hitting memory.
    row = await _afetchone(
        "SELECT "
        "  COUNT(*) AS total, "
        "  COALESCE(SUM(CASE WHEN email_verified = 1 THEN 1 ELSE 0 END),0) AS verified, "
        "  COALESCE(SUM(CASE WHEN is_suspended = 1 THEN 1 ELSE 0 END),0) AS suspended, "
        "  COALESCE(SUM(credits_remaining),0) AS total_credits, "
        "  COALESCE(SUM(CASE WHEN created_at > %s THEN 1 ELSE 0 END),0) AS recent_signups "
        "FROM users",
        [since_iso])
    tier_rows = await _afetchall(
        "SELECT COALESCE(tier,'free') AS tier, COUNT(*) AS n FROM users GROUP BY tier")
    by_tier = {r["tier"]: r["n"] for r in (tier_rows or [])}
    if not row:
        return {"total": 0, "verified": 0, "suspended": 0, "total_credits": 0,
                "recent_signups": 0, "by_tier": by_tier}
    return {
        "total": row["total"] or 0,
        "verified": row["verified"] or 0,
        "suspended": row["suspended"] or 0,
        "total_credits": row["total_credits"] or 0,
        "recent_signups": row["recent_signups"] or 0,
        "by_tier": by_tier,
    }


async def get_user_by_google_id(google_id: str) -> dict | None:
    return await _afetchone("SELECT * FROM users WHERE google_id = %s", [google_id])


async def create_google_user(email: str, google_id: str, display_name: str, avatar_url: str = "") -> int:
    now = datetime.now(timezone.utc).isoformat()
    reset = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    free_credits = TIERS.get("free", {}).get("credits", 5)
    return await _aexec_returning(
        "INSERT INTO users (email, password_hash, display_name, tier, credits_remaining, credits_reset_date, created_at, google_id, auth_provider, email_verified, avatar_url) "
        "VALUES (%s, '', %s, 'free', %s, %s, %s, %s, 'google', 1, %s) RETURNING id",
        [email.lower().strip(), display_name, free_credits, reset, now, google_id, avatar_url])


async def delete_user_sessions(user_id: int):
    await _aexec("DELETE FROM sessions WHERE user_id = %s", [user_id])


async def delete_all_user_data(user_id: int):
    """Delete all data for a user (GDPR Article 17 — Right to Erasure).

    Stability fix (multi-agent bug #18): three tables that store
    per-user rows were absent from this list — agent_run_logs,
    lead_actions, user_learning_profile. Account deletion left their
    rows behind, violating Right to Erasure. Added below.
    """
    stmts = [
        ("DELETE FROM sessions WHERE user_id = %s", [user_id]),
        ("DELETE FROM leads WHERE user_id = %s", [user_id]),
        ("DELETE FROM archived_leads WHERE user_id = %s", [user_id]),
        ("DELETE FROM user_settings WHERE user_id = %s", [user_id]),
        ("DELETE FROM seen_history WHERE user_id = %s", [user_id]),
        ("DELETE FROM seen_fingerprints WHERE user_id = %s", [user_id]),
        ("DELETE FROM domain_blocklist WHERE user_id = %s", [user_id]),
        ("DELETE FROM user_blocked WHERE user_id = %s", [user_id]),
        ("DELETE FROM agent_runs WHERE user_id = %s", [user_id]),
        ("DELETE FROM agent_run_logs WHERE user_id = %s", [user_id]),
        ("DELETE FROM credit_ledger WHERE user_id = %s", [user_id]),
        ("DELETE FROM stripe_events WHERE user_id = %s", [user_id]),
        ("DELETE FROM admin_audit_log WHERE target_user_id = %s", [user_id]),
        ("DELETE FROM lead_feedback WHERE user_id = %s", [user_id]),
        ("DELETE FROM lead_actions WHERE user_id = %s", [user_id]),
        ("DELETE FROM agent_dna WHERE user_id = %s", [user_id]),
        ("DELETE FROM user_learning_profile WHERE user_id = %s", [user_id]),
        ("DELETE FROM hunt_shares WHERE user_id = %s", [user_id]),
        ("DELETE FROM hunt_recipes WHERE user_id = %s", [user_id]),
        ("DELETE FROM checkout_starts WHERE user_id = %s", [user_id]),
        ("DELETE FROM users WHERE id = %s", [user_id]),
    ]
    await _aexec_pipeline(stmts)


async def log_admin_action(admin_user_id: int, target_user_id: int | None, action: str, details: dict, ip: str = ""):
    now = datetime.now(timezone.utc).isoformat()
    details_json = json.dumps(details, ensure_ascii=False, default=str)
    await _aexec(
        "INSERT INTO admin_audit_log (admin_user_id, target_user_id, action, details, ip, created_at) VALUES (%s, %s, %s, %s, %s, %s)",
        [admin_user_id, target_user_id, action, details_json, ip, now])


async def get_admin_audit_log(page: int = 1, page_size: int = 50,
                              target_user_id: int | None = None,
                              admin_user_id: int | None = None,
                              action: str = "") -> dict:
    conditions = []
    params = []
    if target_user_id:
        conditions.append("a.target_user_id = %s")
        params.append(target_user_id)
    if admin_user_id:
        conditions.append("a.admin_user_id = %s")
        params.append(admin_user_id)
    if action:
        conditions.append("a.action LIKE %s")
        params.append(f"%{action}%")
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    count_row = await _afetchone(f"SELECT COUNT(*) as total FROM admin_audit_log a{where}", params)
    total = count_row["total"] if count_row else 0
    offset = (page - 1) * page_size
    rows = await _afetchall(
        f"SELECT a.id, a.admin_user_id, a.target_user_id, a.action, a.details, a.ip, a.created_at, "
        f"u.email as admin_email, t.email as target_email "
        f"FROM admin_audit_log a LEFT JOIN users u ON a.admin_user_id = u.id "
        f"LEFT JOIN users t ON a.target_user_id = t.id{where} ORDER BY a.created_at DESC, a.id DESC LIMIT %s OFFSET %s",
        params + [page_size, offset])
    return {"items": rows, "page": page, "page_size": page_size, "total": total,
            "pages": max(1, (total + page_size - 1) // page_size)}


async def get_recent_stripe_events(limit: int = 50) -> list:
    return await _afetchall(
        "SELECT se.event_id, se.event_type, se.user_id, se.product_id, se.processed_at, u.email as user_email "
        "FROM stripe_events se LEFT JOIN users u ON se.user_id = u.id ORDER BY se.processed_at DESC LIMIT %s",
        [limit])


async def get_recent_credit_events(limit: int = 50) -> list:
    return await _afetchall(
        "SELECT cl.id, cl.user_id, cl.amount, cl.balance_after, cl.reason, cl.reference, cl.created_at, u.email as user_email "
        "FROM credit_ledger cl LEFT JOIN users u ON cl.user_id = u.id "
        "WHERE cl.reason IN ('admin_grant','admin_revoke','admin_set_exact_adjustment','admin_plan_grant','topup','subscription','subscription_renewal','subscription_cancelled') "
        "ORDER BY cl.created_at DESC LIMIT %s",
        [limit])


async def get_billing_anomalies() -> list:
    return await _afetchall(
        "SELECT id, email, tier, credits_remaining, credits_reset_date FROM users WHERE tier != 'free' AND credits_remaining <= 0 ORDER BY email")


async def get_users_paginated(page: int = 1, page_size: int = 25, q: str = "", tier: str = "",
                               verified: str = "", suspended: str = "", low_credits: bool = False,
                               wizard_configured: str = "") -> dict:
    page_size = min(page_size, 100)
    page = max(page, 1)
    conditions = []
    params = []
    if q:
        conditions.append("(email LIKE %s OR display_name LIKE %s)")
        params.extend([f"%{q}%", f"%{q}%"])
    if tier:
        conditions.append("tier = %s")
        params.append(tier)
    if verified == "true":
        conditions.append("email_verified = 1")
    elif verified == "false":
        conditions.append("(email_verified = 0 OR email_verified IS NULL)")
    if suspended == "true":
        conditions.append("is_suspended = 1")
    elif suspended == "false":
        conditions.append("(is_suspended = 0 OR is_suspended IS NULL)")
    if low_credits:
        conditions.append("credits_remaining <= 3")
    _wiz_join = ""
    if wizard_configured in ("true", "false"):
        _wiz_join = " LEFT JOIN user_settings us ON users.id = us.user_id"
        if wizard_configured == "true":
            conditions.append("us.data IS NOT NULL AND us.data != '{}' AND us.data LIKE '%company_name%'")
        else:
            conditions.append("(us.data IS NULL OR us.data = '{}' OR us.data NOT LIKE '%company_name%')")
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    count_row = await _afetchone(f"SELECT COUNT(*) as total FROM users{_wiz_join}{where}", params)
    total = count_row["total"] if count_row else 0
    offset = (page - 1) * page_size
    rows = await _afetchall(
        f"SELECT users.id, users.email, users.display_name, users.tier, users.credits_remaining, "
        f"users.email_verified, users.is_admin, users.is_suspended, users.created_at, users.last_login "
        f"FROM users{_wiz_join}{where} ORDER BY users.created_at DESC, users.id DESC LIMIT %s OFFSET %s",
        params + [page_size, offset])
    return {"items": rows, "page": page, "page_size": page_size, "total": total,
            "pages": max(1, (total + page_size - 1) // page_size)}


async def get_user_detail_bundle(user_id: int) -> dict | None:
    user = await get_user_by_id(user_id)
    if not user:
        return None
    result = {}
    result["profile"] = {
        "id": user["id"], "email": user["email"],
        "display_name": user.get("display_name", ""),
        "tier": user.get("tier", "free"),
        "credits_remaining": user.get("credits_remaining", 0),
        "email_verified": bool(user.get("email_verified")),
        "is_admin": bool(user.get("is_admin")),
        "is_suspended": bool(user.get("is_suspended")),
        "auth_provider": user.get("auth_provider", "email"),
        "created_at": user.get("created_at", ""),
        "last_login": user.get("last_login", ""),
    }
    try:
        tier_info = TIERS.get(user.get("tier", "free"), TIERS["free"])
        credit_history = await get_credit_history(user_id, limit=20)
        result["billing"] = {"tier": user.get("tier", "free"), "tier_name": tier_info.get("name", "Free"),
            "monthly_credits": tier_info.get("credits", 0), "credits_remaining": user.get("credits_remaining", 0),
            "credits_reset_date": user.get("credits_reset_date", ""), "recent_ledger": credit_history}
    except Exception:
        result["billing"] = {"error": "failed to load"}
    try:
        settings = await get_settings(user_id)
        wizard = settings.get("wizard", {})
        brain = wizard.get("normalized_hunt_profile", {})
        dossier = wizard.get("training_dossier", {})
        result["wizard"] = {
            "configured": bool(wizard.get("company_name") or wizard.get("_site_scanned")),
            "company_name": wizard.get("company_name", ""),
            "business_description": wizard.get("business_description", "")[:200],
            "archetype": wizard.get("archetype", ""),
            "archetype_confidence": wizard.get("archetype_confidence", 0),
            "has_brain": bool(brain), "brain_version": brain.get("hunt_brain_version", 0),
            "brain_source": brain.get("source", ""), "has_dossier": bool(dossier),
            "dossier_version": dossier.get("training_dossier_version", 0),
            "interview_complete": bool(wizard.get("_interview_complete")),
            "train_count": wizard.get("_train_count", 0),
            "services": brain.get("services_clean", wizard.get("services", [])),
            "industries": brain.get("preferred_industries", []),
            "buyer_roles": brain.get("buyer_roles_clean", []),
            "profile_confidence": brain.get("profile_confidence", 0),
        }
    except Exception:
        result["wizard"] = {"error": "failed to load"}
    try:
        lead_row = await _afetchone("SELECT COUNT(*) as total FROM leads WHERE user_id = %s", [user_id])
        result["lead_stats"] = {"total": lead_row["total"] if lead_row else 0}
        try:
            archived_row = await _afetchone("SELECT COUNT(*) as total FROM archived_leads WHERE user_id = %s", [user_id])
            result["lead_stats"]["archived"] = archived_row["total"] if archived_row else 0
        except Exception:
            result["lead_stats"]["archived"] = 0
    except Exception:
        result["lead_stats"] = {"total": 0, "archived": 0, "error": "failed to load"}
    try:
        runs = await _afetchall("SELECT id, status, leads_found, started_at, ended_at FROM agent_runs WHERE user_id = %s ORDER BY started_at DESC LIMIT 5", [user_id])
        result["agent"] = {"recent_runs": runs or []}
    except Exception:
        result["agent"] = {"recent_runs": []}
    try:
        sess_row = await _afetchone("SELECT COUNT(*) as count FROM sessions WHERE user_id = %s", [user_id])
        result["sessions"] = {"active_count": sess_row["count"] if sess_row else 0}
    except Exception:
        result["sessions"] = {"active_count": 0}
    try:
        webhooks = await _afetchall("SELECT event_id, event_type, product_id, processed_at FROM stripe_events WHERE user_id = %s ORDER BY processed_at DESC LIMIT 5", [user_id])
        result["payments"] = {"recent_events": webhooks or []}
    except Exception:
        result["payments"] = {"recent_events": []}
    return result


# ── Sessions ──

async def create_session(token: str, user_id: int):
    expires = (datetime.now(timezone.utc) + timedelta(hours=SESSION_EXPIRY_HOURS)).isoformat()
    await _aexec("INSERT INTO sessions (token, user_id, expires_at) VALUES (%s, %s, %s)", [token, user_id, expires])


async def get_session(token: str) -> dict | None:
    row = await _afetchone("SELECT * FROM sessions WHERE token = %s", [token])
    if not row:
        return None
    try:
        exp = datetime.fromisoformat(str(row["expires_at"]))
        if not exp.tzinfo:
            exp = exp.replace(tzinfo=timezone.utc)  # Assume UTC if naive
        # Stability fix (Perplexity bug #51): use <= so a session at
        # the exact expiry instant is rejected. With < the session
        # would still be accepted at exp == now, which contradicts the
        # "valid only before expiry" spec. Microsecond-precision edge
        # case but cleaner semantics.
        if exp <= datetime.now(timezone.utc):
            await _aexec("DELETE FROM sessions WHERE token = %s", [token])
            return None
    except (ValueError, TypeError):
        # Corrupt expires_at — delete the session
        await _aexec("DELETE FROM sessions WHERE token = %s", [token])
        return None
    return row


async def delete_session(token: str):
    await _aexec("DELETE FROM sessions WHERE token = %s", [token])


async def cleanup_expired_sessions():
    now = datetime.now(timezone.utc).isoformat()
    # Use <= so a session whose expires_at is exactly `now` gets
    # cleaned. get_session() rejects on the same boundary (<=),
    # otherwise zombie expired rows accumulate at the boundary tick.
    await _aexec("DELETE FROM sessions WHERE expires_at <= %s", [now])


# ── Leads ──

async def get_leads(user_id: int, limit: int | None = None, offset: int = 0) -> list:
    # Safety ceiling: a single call never returns more than 10k leads even
    # if callers don't explicitly paginate. Caps memory + bandwidth and
    # prevents a compromised session from siphoning the entire table in
    # one request. Frontend can page with limit/offset for user >10k leads.
    HARD_CAP = 10000
    effective_limit = HARD_CAP if limit is None else max(1, min(limit, HARD_CAP))
    # Stability fix (Perplexity bug #38): id DESC tiebreaker so two leads
    # sharing the same created_at don't swap positions between page
    # fetches. PostgreSQL only guarantees deterministic LIMIT/OFFSET
    # results when ORDER BY uniquely identifies each row.
    sql = "SELECT lead_id, data FROM leads WHERE user_id = %s ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s"
    rows = await _afetchall(sql, [user_id, effective_limit, max(0, offset)])
    results = []
    for r in rows:
        try:
            d = json.loads(r["data"])
        except (json.JSONDecodeError, TypeError):
            d = {}
        d["lead_id"] = r["lead_id"]
        results.append(d)
    return results


async def get_leads_count(user_id: int) -> int:
    row = await _afetchone("SELECT COUNT(*) as n FROM leads WHERE user_id = %s", [user_id])
    return int(row["n"]) if row else 0


async def get_lead(user_id: int, lead_id: str) -> dict | None:
    row = await _afetchone("SELECT data FROM leads WHERE user_id = %s AND lead_id = %s", [user_id, lead_id])
    if not row:
        return None
    try:
        d = json.loads(row["data"])
    except (json.JSONDecodeError, TypeError):
        d = {}
    # `leads.data` is supposed to be a JSON object, but valid JSON can
    # also be `"null"` / `123` / `[…]`. Subscripting a non-dict here
    # would raise TypeError and crash the whole get_lead call.
    if not isinstance(d, dict):
        d = {}
    d["lead_id"] = lead_id
    return d


_GENERIC_EMAIL_PREFIX = re.compile(
    r"^(info|hello|contact|general|admin|support|noreply|no-reply|office|enquir|"
    r"sales|team|hr|jobs|careers|press|media|marketing|billing|accounts|webmaster|"
    r"hostmaster|abuse)@",
    re.I)


def _merge_lead_sync(user_id: int, lead_id: str, mutator) -> dict | None:
    """Atomic read-modify-write for a single lead row.

    Stability fix (Perplexity bug #79): /api/update used to do
    get_lead + Python mutation + upsert_lead as three separate calls,
    which is the classic lost-update race — concurrent edits to
    different fields from different CRM panels (status dropdown, notes,
    edit form) silently clobbered each other.

    Helper takes a row lock via SELECT FOR UPDATE, hands the parsed
    dict to `mutator(lead)` (which returns the new dict), then upserts
    inside the same transaction. Returns the new dict on success or
    None if the lead doesn't exist.

    Note: side effects the route wants to fire AFTER the merge (e.g.
    save_lead_feedback for status outcomes) should run AFTER this
    helper returns — not inside the mutator — so they don't extend
    the lock window.
    """
    if not _pool:
        raise RuntimeError("Database connection pool not available")
    conn = get_conn()
    try:
        # Use the driver-agnostic cursor + xlate so this helper works
        # in SQLite mode too (the translator strips `FOR UPDATE` which
        # SQLite doesn't support; the row-level lock degrades to the
        # _SqliteSerial wrapper's whole-DB lock — fine in single-user
        # local mode).
        cur = _cursor(conn)
        cur.execute(
            _xlate("SELECT data FROM leads WHERE user_id = %s AND lead_id = %s FOR UPDATE"),
            [user_id, lead_id])
        row = cur.fetchone()
        if not row:
            conn.rollback()
            return None
        try:
            current = json.loads(row["data"])
            if not isinstance(current, dict):
                current = {}
        except (json.JSONDecodeError, TypeError):
            current = {}
        new_data = mutator(current)
        if not isinstance(new_data, dict):
            raise ValueError("merge_lead mutator must return a dict")
        # Compute generic-email flag like upsert_lead does so we keep
        # consistent semantics across write paths.
        _email = (new_data.get("contact_email") or "").strip()
        new_data["_is_generic_email"] = bool(_email and _GENERIC_EMAIL_PREFIX.match(_email))
        data_json = json.dumps(new_data, ensure_ascii=False, default=str)
        fit_score = new_data.get("fit_score", 0) or 0
        org_name = new_data.get("org_name", "") or ""
        country = new_data.get("country", "") or ""
        email_status = new_data.get("email_status", "new") or "new"
        cur.execute(
            "UPDATE leads SET data=%s, fit_score=%s, org_name=%s, country=%s, email_status=%s "
            "WHERE user_id = %s AND lead_id = %s",
            [data_json, fit_score, org_name, country, email_status, user_id, lead_id])
        conn.commit()
        return new_data
    except Exception:
        _safe_rollback(conn)
        raise
    finally:
        put_conn(conn)


async def merge_lead(user_id: int, lead_id: str, mutator) -> dict | None:
    """Async wrapper around _merge_lead_sync. See docstring."""
    return await asyncio.to_thread(_merge_lead_sync, user_id, lead_id, mutator)


async def upsert_lead(user_id: int, lead_id: str, data: dict) -> bool:
    """Insert OR update a lead. Returns True if a NEW row was inserted,
    False if an existing row was updated.

    Stability fix (Perplexity bug #59): agent code uses the return
    value to decide whether to deduct a credit. lead_id is a
    deterministic hash from the prospect fingerprint, so two
    different URLs on the same domain can produce the same lead_id.
    Without this signal, re-discovery in a later run would silently
    UPDATE the row AND deduct a credit again — real money loss for
    the user.

    The PostgreSQL trick: in `... ON CONFLICT DO UPDATE RETURNING ...`
    the system column `xmax` equals 0 when the row was actually
    inserted, and non-zero when it was updated.
    """
    now = datetime.now(timezone.utc).isoformat()
    # Compute a stable 'generic email' flag on save so the frontend can
    # warn the user that info@/sales@/support@ addresses are low-value
    # (higher bounce rate, worse open rate, typically no decision-maker).
    # Centralised here so every write path (agent enrichment, manual edit,
    # research refresh, bulk import) gets the flag without each caller
    # having to remember to set it.
    _email = (data.get("contact_email") or "").strip()
    data["_is_generic_email"] = bool(_email and _GENERIC_EMAIL_PREFIX.match(_email))
    data_json = json.dumps(data, ensure_ascii=False, default=str)
    fit_score = data.get("fit_score", 0) or 0
    org_name = data.get("org_name", "") or ""
    country = data.get("country", "") or ""
    email_status = data.get("email_status", "new") or "new"
    # ON CONFLICT preserves email_status when the user has already
    # advanced it past 'new' (manually marked verified / contacted /
    # replied / etc.). A re-run of the same hunt would otherwise reset
    # their CRM state to 'new' and erase their work. fit_score is also
    # preserved when the existing row is higher — re-finding a lead
    # at a lower score (different page, less context) shouldn't
    # downgrade a previous high-quality assessment.
    row = await _afetchone(
        "INSERT INTO leads (user_id, lead_id, data, fit_score, org_name, country, email_status, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (user_id, lead_id) DO UPDATE SET "
        "  data=EXCLUDED.data, "
        "  fit_score=CASE WHEN leads.fit_score > EXCLUDED.fit_score THEN leads.fit_score ELSE EXCLUDED.fit_score END, "
        "  org_name=EXCLUDED.org_name, "
        "  country=EXCLUDED.country, "
        "  email_status=CASE WHEN leads.email_status IS NOT NULL AND leads.email_status <> 'new' THEN leads.email_status ELSE EXCLUDED.email_status END "
        "RETURNING (xmax = 0) AS was_inserted",
        [user_id, lead_id, data_json, fit_score, org_name, country, email_status, now])
    return bool(row and row.get("was_inserted"))


async def save_leads_bulk(user_id: int, leads: list):
    now = datetime.now(timezone.utc).isoformat()
    stmts = []
    for lead in leads:
        lid = lead.get("lead_id", "")
        if not lid:
            continue
        data_json = json.dumps(lead, ensure_ascii=False, default=str)
        fs = lead.get("fit_score", 0) or 0
        on = lead.get("org_name", "") or ""
        co = lead.get("country", "") or ""
        es = lead.get("email_status", "new") or "new"
        ca = lead.get("created_at", now)
        # Preserve a higher pre-existing fit_score the same way
        # `upsert_lead` does — bulk path was downgrading high-quality
        # assessments when a re-discovery batch came in with lower
        # scores. Use EXCLUDED + CASE so the higher of (existing, new)
        # wins, and the rest of the columns reflect the latest crawl.
        stmts.append((
            "INSERT INTO leads (user_id, lead_id, data, fit_score, org_name, country, email_status, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (user_id, lead_id) DO UPDATE SET "
            "  data = EXCLUDED.data, "
            "  fit_score = CASE WHEN leads.fit_score > EXCLUDED.fit_score "
            "              THEN leads.fit_score ELSE EXCLUDED.fit_score END, "
            "  org_name = EXCLUDED.org_name, "
            "  country = EXCLUDED.country, "
            # Stability fix (audit wave 26): the bulk save used to
            # blindly overwrite `email_status` with EXCLUDED, which is
            # always "new" for re-discovered leads (the agent's lead
            # dicts default to "new"). When a user had already moved
            # the lead to verified / contacted / replied / won via
            # CRM mutations, the next hunt that re-found that domain
            # silently reset the column back to "new" — wiping
            # outreach progress without warning. `upsert_lead` already
            # preserves the user's column with a CASE expression; the
            # bulk path was missing the same guard.
            "  email_status = CASE "
            "    WHEN leads.email_status IS NOT NULL "
            "         AND leads.email_status <> 'new' "
            "    THEN leads.email_status ELSE EXCLUDED.email_status END",
            [user_id, lid, data_json, fs, on, co, es, ca]))
    if stmts:
        await _aexec_pipeline(stmts)


async def delete_lead(user_id: int, lead_id: str) -> dict | None:
    lead = await get_lead(user_id, lead_id)
    if not lead:
        return None
    now = datetime.now(timezone.utc).isoformat()
    await _aexec_pipeline([
        ("DELETE FROM leads WHERE user_id = %s AND lead_id = %s", [user_id, lead_id]),
        ("INSERT INTO archived_leads (user_id, lead_id, data, archived_at) VALUES (%s, %s, %s, %s)",
         [user_id, lead_id, json.dumps(lead, ensure_ascii=False, default=str), now]),
    ])
    return lead


async def restore_lead(user_id: int, lead_id: str) -> dict | None:
    # Stability fix (Perplexity bug #57): the previous version did
    # DELETE archived → INSERT leads ... ON CONFLICT DO NOTHING in
    # one pipeline transaction. If a live row with the same lead_id
    # already existed (agent re-found the same domain after the user
    # archived it), the archive was deleted but the restore insert
    # silently skipped — the user lost both copies. Now we insert
    # FIRST, check rowcount, and only delete the archive when the
    # restore actually wrote a new row.
    row = await _afetchone("SELECT data FROM archived_leads WHERE user_id = %s AND lead_id = %s", [user_id, lead_id])
    if not row:
        return None
    try:
        data = json.loads(row["data"])
    except (json.JSONDecodeError, TypeError) as _je:
        # Corrupted archive row — emit a warning so the user knows the
        # restore inserted a placeholder rather than silently writing an
        # empty lead. Don't bail entirely; the surrounding flow expects a
        # non-None return on success.
        print(f"[huntova] warning: archived lead {lead_id} JSON corrupted "
              f"({type(_je).__name__}); restoring with empty payload",
              file=sys.stderr)
        data = {}
    now = datetime.now(timezone.utc).isoformat()
    data_json = json.dumps(data, ensure_ascii=False, default=str)
    inserted = await _aexec_rowcount(
        "INSERT INTO leads (user_id, lead_id, data, fit_score, org_name, country, email_status, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (user_id, lead_id) DO NOTHING",
        [user_id, lead_id, data_json, data.get("fit_score", 0) or 0, data.get("org_name", "") or "",
         data.get("country", "") or "", data.get("email_status", "new") or "new", now])
    if inserted == 0:
        # A live lead with this id already exists — leave the archive
        # in place so the user doesn't lose data on a no-op restore.
        return None
    await _aexec("DELETE FROM archived_leads WHERE user_id = %s AND lead_id = %s", [user_id, lead_id])
    return data


async def permanent_delete_lead(user_id: int, lead_id: str):
    # Cascade to lead_feedback and lead_actions so orphan rows don't linger
    # in the DNA refinement + scoring pipeline after a user deletes a lead.
    # Previously only leads + archived_leads were cleaned, leaving stale
    # feedback signals tied to a lead_id that no longer existed.
    await _aexec_pipeline([
        ("DELETE FROM leads WHERE user_id = %s AND lead_id = %s", [user_id, lead_id]),
        ("DELETE FROM archived_leads WHERE user_id = %s AND lead_id = %s", [user_id, lead_id]),
        ("DELETE FROM lead_feedback WHERE user_id = %s AND lead_id = %s", [user_id, lead_id]),
        ("DELETE FROM lead_actions WHERE user_id = %s AND lead_id = %s", [user_id, lead_id]),
    ])


# ── Settings ──

async def get_settings(user_id: int) -> dict:
    row = await _afetchone("SELECT data FROM user_settings WHERE user_id = %s", [user_id])
    if not row:
        return {}
    try:
        return json.loads(row["data"])
    except (json.JSONDecodeError, TypeError):
        return {}


async def save_settings(user_id: int, data: dict):
    data_json = json.dumps(data, ensure_ascii=False, default=str)
    await _aexec(
        "INSERT INTO user_settings (user_id, data) VALUES (%s, %s) "
        "ON CONFLICT (user_id) DO UPDATE SET data = %s",
        [user_id, data_json, data_json])


def _merge_settings_sync(user_id: int, mutator) -> dict:
    """Atomic read-modify-write for user_settings.data.

    Stability fix (Perplexity bug #76): plain get_settings + mutate +
    save_settings is a classic lost-update pattern — two endpoints
    that both load the same JSON, mutate different keys, and both
    save will silently overwrite each other (whoever writes last
    wins, the other's keys disappear).

    This helper runs SELECT...FOR UPDATE → mutator(data) → upsert in
    ONE transaction. Concurrent writers serialize on the row lock
    so each one sees the previous winner's blob before applying its
    own delta. Use this for any narrow-scoped settings update where
    you don't want to clobber concurrent writers.

    `mutator` is a callable that takes the current dict (or {}) and
    returns the new dict. It runs WHILE the row is locked, so keep
    it cheap (no I/O).
    """
    if not _pool:
        raise RuntimeError("Database connection pool not available")
    conn = get_conn()
    try:
        # Driver-agnostic — same reasoning as _merge_lead_sync above.
        cur = _cursor(conn)
        # SELECT FOR UPDATE waits for any in-flight writer on this row.
        # If the row doesn't exist yet, no lock is taken — but the
        # subsequent INSERT ON CONFLICT serialises against any other
        # concurrent insert via the unique key.
        cur.execute(_xlate("SELECT data FROM user_settings WHERE user_id = %s FOR UPDATE"), [user_id])
        row = cur.fetchone()
        try:
            current = json.loads(row["data"]) if row else {}
            if not isinstance(current, dict):
                current = {}
        except (json.JSONDecodeError, TypeError):
            current = {}
        new_data = mutator(current)
        if not isinstance(new_data, dict):
            raise ValueError("merge_settings mutator must return a dict")
        data_json = json.dumps(new_data, ensure_ascii=False, default=str)
        cur.execute(
            "INSERT INTO user_settings (user_id, data) VALUES (%s, %s) "
            "ON CONFLICT (user_id) DO UPDATE SET data = %s",
            [user_id, data_json, data_json])
        conn.commit()
        return new_data
    except Exception:
        _safe_rollback(conn)
        raise
    finally:
        put_conn(conn)


async def merge_settings(user_id: int, mutator) -> dict:
    """Async wrapper around _merge_settings_sync. See its docstring."""
    return await asyncio.to_thread(_merge_settings_sync, user_id, mutator)


# ── Agent DNA ──

async def get_agent_dna(user_id: int) -> dict | None:
    row = await _afetchone("SELECT dna_json, version, generated_at FROM agent_dna WHERE user_id = %s", [user_id])
    if not row:
        return None
    try:
        # `dna_json` can be NULL on freshly-migrated rows; json.loads(None)
        # raises TypeError (not ValueError), which would have crashed the
        # caller. Treat null as "no DNA yet".
        dna = json.loads(row["dna_json"]) if row.get("dna_json") else None
        if not dna:
            return None
        dna["version"] = row.get("version", 1)
        dna["generated_at"] = row.get("generated_at", "")
        return dna
    except (json.JSONDecodeError, ValueError, TypeError):
        return None


async def save_agent_dna(user_id: int, dna: dict):
    now = datetime.now(timezone.utc).isoformat()
    version = dna.get("version", 1)
    dna_json = json.dumps(dna, ensure_ascii=False, default=str)
    await _aexec(
        "INSERT INTO agent_dna (user_id, dna_json, version, generated_at) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (user_id) DO UPDATE SET dna_json = %s, version = %s, generated_at = %s",
        [user_id, dna_json, version, now, dna_json, version, now])


# ── Lead Feedback ──

async def save_lead_feedback(user_id: int, lead_id: str, signal: str, reason: str = ""):
    # Stability fix (Perplexity bug #77): the previous version was a
    # blind INSERT — double-clicks, accidental re-submits, browser
    # retries all produced duplicate rows for the same user+lead.
    # get_lead_feedback_count uses raw row count, so duplicates
    # inflated the DNA-regen trigger (every 10 signals) and skewed
    # the score-band stats AI refinement reads. Now upsert: one row
    # per (user_id, lead_id), latest signal+reason wins. The unique
    # index is created in init_db_sync below; here we use the same
    # constraint name so ON CONFLICT resolves it.
    now = datetime.now(timezone.utc).isoformat()
    await _aexec(
        "INSERT INTO lead_feedback (user_id, lead_id, signal, reason, created_at) "
        "VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT (user_id, lead_id) DO UPDATE SET "
        "signal = EXCLUDED.signal, reason = EXCLUDED.reason, created_at = EXCLUDED.created_at",
        [user_id, lead_id, signal, reason, now])


async def get_lead_feedback_count(user_id: int) -> dict:
    good = await _afetchone("SELECT COUNT(*) as c FROM lead_feedback WHERE user_id = %s AND signal = 'good'", [user_id])
    bad = await _afetchone("SELECT COUNT(*) as c FROM lead_feedback WHERE user_id = %s AND signal = 'bad'", [user_id])
    return {"good": good["c"] if good else 0, "bad": bad["c"] if bad else 0}


async def get_lead_feedback_recent(user_id: int, signal: str, limit: int = 10) -> list:
    rows = await _afetchall(
        "SELECT lf.lead_id, lf.signal, l.data FROM lead_feedback lf "
        "LEFT JOIN leads l ON lf.user_id = l.user_id AND lf.lead_id = l.lead_id "
        # Tiebreaker on lf.id DESC — same stability fix already applied in
        # get_leads(). Without it, two feedback rows sharing a created_at
        # (bulk teach imports, rapid Good Fit clicks) return in
        # driver-defined order that's unstable across calls, breaking
        # any UI that paginates feedback.
        "WHERE lf.user_id = %s AND lf.signal = %s ORDER BY lf.created_at DESC, lf.id DESC LIMIT %s",
        [user_id, signal, limit])
    results = []
    for r in rows:
        try:
            d = json.loads(r.get("data") or "{}")
            d["lead_id"] = r["lead_id"]
            d["signal"] = r["signal"]
            results.append(d)
        except (json.JSONDecodeError, ValueError):
            pass
    return results


# ── Seen History ──

async def get_seen_urls(user_id: int) -> set:
    rows = await _afetchall("SELECT url_hash FROM seen_history WHERE user_id = %s", [user_id])
    return {r["url_hash"] for r in rows}


async def add_seen_url(user_id: int, url_hash: str):
    try:
        await _aexec("INSERT INTO seen_history (user_id, url_hash) VALUES (%s, %s) ON CONFLICT DO NOTHING", [user_id, url_hash])
    except Exception:
        pass


async def add_seen_urls_bulk(user_id: int, url_hashes: list):
    stmts = [("INSERT INTO seen_history (user_id, url_hash) VALUES (%s, %s) ON CONFLICT DO NOTHING", [user_id, h]) for h in url_hashes]
    if stmts:
        try:
            await _aexec_pipeline(stmts)
        except Exception:
            pass


async def get_seen_fingerprints(user_id: int) -> set:
    rows = await _afetchall("SELECT fingerprint FROM seen_fingerprints WHERE user_id = %s", [user_id])
    return {r["fingerprint"] for r in rows}


async def add_seen_fingerprint(user_id: int, fingerprint: str):
    try:
        await _aexec("INSERT INTO seen_fingerprints (user_id, fingerprint) VALUES (%s, %s) ON CONFLICT DO NOTHING", [user_id, fingerprint])
    except Exception:
        pass


# ── Domain Blocklist ──

async def get_domain_blocklist(user_id: int) -> dict:
    rows = await _afetchall("SELECT domain, fail_count FROM domain_blocklist WHERE user_id = %s", [user_id])
    return {r["domain"]: r["fail_count"] for r in rows}


async def record_domain_fail(user_id: int, domain: str):
    await _aexec(
        "INSERT INTO domain_blocklist (user_id, domain, fail_count) VALUES (%s, %s, 1) "
        "ON CONFLICT (user_id, domain) DO UPDATE SET fail_count = domain_blocklist.fail_count + 1",
        [user_id, domain])


async def set_domain_fail_count(user_id: int, domain: str, count: int):
    await _aexec(
        "INSERT INTO domain_blocklist (user_id, domain, fail_count) VALUES (%s, %s, %s) "
        "ON CONFLICT (user_id, domain) DO UPDATE SET fail_count = %s",
        [user_id, domain, count, count])


# ── User Blocked ──

async def get_user_blocked(user_id: int) -> dict:
    rows = await _afetchall("SELECT block_type, value FROM user_blocked WHERE user_id = %s", [user_id])
    result = {"domains": [], "org_names": []}
    for r in rows:
        if r["block_type"] == "domain":
            result["domains"].append(r["value"])
        elif r["block_type"] == "org_name":
            result["org_names"].append(r["value"])
    return result


async def add_user_block(user_id: int, block_type: str, value: str):
    try:
        await _aexec(
            "INSERT INTO user_blocked (user_id, block_type, value) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            [user_id, block_type, value])
    except Exception:
        pass


# ── Agent Run Logs ──

async def save_agent_run_log(user_id: int, run_id: int, log_text: str, leads_found: int = 0, queries_run: int = 0, urls_checked: int = 0):
    now = datetime.now(timezone.utc).isoformat()
    await _aexec(
        "INSERT INTO agent_run_logs (user_id, run_id, log_text, leads_found, queries_run, urls_checked, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        [user_id, run_id, log_text, leads_found, queries_run, urls_checked, now])


async def get_agent_run_logs(user_id: int, limit: int = 10) -> list:
    return await _afetchall(
        "SELECT id, run_id, leads_found, queries_run, urls_checked, created_at FROM agent_run_logs WHERE user_id = %s ORDER BY created_at DESC LIMIT %s",
        [user_id, limit])


# ── Lead Actions (outcome tracking) ──

async def save_lead_action(user_id: int, lead_id: str, action_type: str, score_band: str = "", confidence_band: str = "", meta: str = "{}"):
    now = datetime.now(timezone.utc).isoformat()
    await _aexec(
        "INSERT INTO lead_actions (user_id, lead_id, action_type, score_band, confidence_band, meta, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        [user_id, lead_id, action_type, score_band, confidence_band, meta, now])


async def get_action_analytics(user_id: int) -> dict:
    """Get action counts by score band and confidence band for a user."""
    by_band = await _afetchall(
        "SELECT score_band, action_type, COUNT(*) as count FROM lead_actions WHERE user_id = %s GROUP BY score_band, action_type ORDER BY score_band, count DESC",
        [user_id])
    by_conf = await _afetchall(
        "SELECT confidence_band, action_type, COUNT(*) as count FROM lead_actions WHERE user_id = %s GROUP BY confidence_band, action_type ORDER BY confidence_band, count DESC",
        [user_id])
    totals = await _afetchall(
        "SELECT action_type, COUNT(*) as count FROM lead_actions WHERE user_id = %s GROUP BY action_type ORDER BY count DESC",
        [user_id])
    return {"by_score_band": by_band or [], "by_confidence": by_conf or [], "totals": totals or []}


# ── User Learning Profile ──

async def get_learning_profile(user_id: int):
    return await _afetchone("SELECT * FROM user_learning_profile WHERE user_id = %s", [user_id])


async def save_learning_profile(user_id: int, preferences: str, instruction_summary: str, signals_processed: int, version: int):
    now = datetime.now(timezone.utc).isoformat()
    await _aexec(
        "INSERT INTO user_learning_profile (user_id, preferences, instruction_summary, signals_processed, version, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (user_id) DO UPDATE SET preferences = %s, instruction_summary = %s, signals_processed = %s, version = %s, updated_at = %s",
        [user_id, preferences, instruction_summary, signals_processed, version, now,
         preferences, instruction_summary, signals_processed, version, now])


async def get_all_feedback_for_profile(user_id: int):
    rows = await _afetchall(
        "SELECT lf.lead_id, lf.signal, lf.reason, lf.created_at, l.data FROM lead_feedback lf "
        "LEFT JOIN leads l ON lf.user_id = l.user_id AND lf.lead_id = l.lead_id "
        "WHERE lf.user_id = %s ORDER BY lf.created_at DESC",
        [user_id])
    results = []
    for r in (rows or []):
        try:
            d = json.loads(r.get("data") or "{}")
            d["lead_id"] = r["lead_id"]
            d["signal"] = r["signal"]
            d["reason"] = r.get("reason", "")
            d["feedback_at"] = r["created_at"]
            results.append(d)
        except Exception:
            pass
    return results


# ── Agent Runs ──

async def create_agent_run(user_id: int) -> int:
    now = datetime.now(timezone.utc).isoformat()
    return await _aexec_returning(
        "INSERT INTO agent_runs (user_id, status, started_at) VALUES (%s, 'running', %s) RETURNING id",
        [user_id, now])


async def update_agent_run(run_id: int, **fields):
    if not fields:
        return
    sets = ", ".join(f"{k} = %s" for k in fields)
    vals = list(fields.values()) + [run_id]
    await _aexec(f"UPDATE agent_runs SET {sets} WHERE id = %s", vals)


async def repair_stale_agent_runs() -> int:
    """Reap orphaned 'running' agent_runs on boot.

    A row with status='running' AND ended_at IS NULL is an orphan from a
    crashed or restarted process — the in-memory thread is gone but the DB
    still claims the run is live. Without this, admin views show stale
    'running' state forever.
    """
    now = datetime.now(timezone.utc).isoformat()
    return await _aexec_rowcount(
        "UPDATE agent_runs SET status = 'error', ended_at = %s "
        "WHERE status = 'running' AND ended_at IS NULL",
        [now])


async def get_user_stats(user_id: int) -> dict:
    row = await _afetchone("SELECT COUNT(*) as total, COALESCE(SUM(leads_found), 0) as total_leads FROM agent_runs WHERE user_id = %s", [user_id])
    runs = row if row else {"total": 0, "total_leads": 0}
    row2 = await _afetchone("SELECT COUNT(*) as count FROM leads WHERE user_id = %s", [user_id])
    lead_count = row2["count"] if row2 else 0
    return {"runs": runs["total"] or 0, "total_leads_found": runs["total_leads"] or 0, "current_leads": lead_count}


# ── Admin: Agent Runs ──

async def get_all_agent_runs(page: int = 1, page_size: int = 50, user_id: int = None, status: str = None) -> dict:
    """Paginated list of all agent runs across users (for admin)."""
    where = []
    params = []
    if user_id:
        where.append("r.user_id = %s")
        params.append(user_id)
    if status:
        where.append("r.status = %s")
        params.append(status)
    where_sql = " AND ".join(where) if where else "1=1"
    count_row = await _afetchone(f"SELECT COUNT(*) as total FROM agent_runs r WHERE {where_sql}", params)
    total = count_row["total"] if count_row else 0
    offset = (page - 1) * page_size
    params_q = list(params) + [page_size, offset]
    rows = await _afetchall(
        f"SELECT r.id, r.user_id, r.status, r.leads_found, r.ai_calls, r.queries_total, r.queries_done, r.started_at, r.ended_at, u.email "
        f"FROM agent_runs r LEFT JOIN users u ON r.user_id = u.id WHERE {where_sql} ORDER BY r.started_at DESC, r.id DESC LIMIT %s OFFSET %s",
        params_q)
    return {"items": rows or [], "total": total, "pages": max(1, -(-total // page_size)), "page": page}


async def get_agent_run_detail(run_id: int) -> dict:
    """Single run with its log text."""
    run = await _afetchone(
        "SELECT r.*, u.email FROM agent_runs r LEFT JOIN users u ON r.user_id = u.id WHERE r.id = %s", [run_id])
    logs = await _afetchall(
        "SELECT id, log_text, leads_found, queries_run, urls_checked, created_at FROM agent_run_logs WHERE run_id = %s ORDER BY created_at ASC", [run_id])
    return {"run": run, "logs": logs or []}


async def get_recent_errors(limit: int = 50) -> list:
    """Recent failed/errored runs for incidents view."""
    return await _afetchall(
        "SELECT r.id, r.user_id, r.status, r.leads_found, r.started_at, r.ended_at, u.email "
        "FROM agent_runs r LEFT JOIN users u ON r.user_id = u.id "
        "WHERE r.status IN ('error', 'crashed') ORDER BY r.started_at DESC LIMIT %s",
        [limit]) or []


# ── Shareable hunt replays (Feature F1) ──
# Snapshot, not live-link: the public page is frozen at share time so
# later CRM edits (status, notes, deletions) don't change what visitors
# see. Also keeps the public surface independent of internal lead
# storage — we choose what's safe to expose at snapshot time.

async def create_hunt_share(user_id: int, run_id: int | None, leads: list,
                             hunt_meta: dict | None = None, title: str = "",
                             expires_at: str | None = None) -> str:
    """Create a shareable snapshot, return the slug.

    `leads` should already be sanitised by the caller (only public-safe
    fields). `hunt_meta` is a small dict — country list, query count,
    finished_at — for context on the public page. The slug is
    URL-safe and ~11 chars.
    """
    now = datetime.now(timezone.utc).isoformat()
    snapshot = {
        "leads": leads or [],
        "meta": hunt_meta or {},
    }
    snapshot_json = json.dumps(snapshot, ensure_ascii=False, default=str)
    # 8-byte slug = 64 bits of entropy. Collision probability per-pair
    # is ~5e-20, so a single retry is plenty. Still, wrap the INSERT in
    # an integrity-error retry loop so a freak collision (or, more
    # likely, a buggy test re-using a fixture slug) doesn't crash the
    # request — just regenerate.
    title_safe = (title or "")[:200]
    last_err: Exception | None = None
    for _attempt in range(3):
        slug = secrets.token_urlsafe(8)
        try:
            await _aexec(
                "INSERT INTO hunt_shares (slug, user_id, run_id, snapshot, title, created_at, expires_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                [slug, user_id, run_id, snapshot_json, title_safe, now, expires_at])
            return slug
        except Exception as _ie:
            last_err = _ie
            # Only retry on plausible PK collisions; if the error names
            # NotNull / Foreign / etc, fall through and re-raise.
            _msg = str(_ie).lower()
            if "unique" in _msg or "primary key" in _msg or "duplicate" in _msg:
                continue
            raise
    # Three collisions in a row is essentially impossible — re-raise.
    raise last_err if last_err else RuntimeError("hunt_share: slug allocation failed")


async def get_hunt_share(slug: str) -> dict | None:
    """Public lookup by slug. Returns None if missing, revoked, or
    expired. Increments view_count on success (best-effort, non-fatal).
    """
    row = await _afetchone(
        "SELECT slug, user_id, run_id, snapshot, title, revoked, view_count, "
        "created_at, expires_at FROM hunt_shares WHERE slug = %s",
        [slug])
    if not row:
        return None
    if int(row.get("revoked") or 0):
        return None
    expires_at = row.get("expires_at")
    if expires_at:
        try:
            _exp = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            # Legacy rows / SQLite TIMESTAMP columns may produce a naive
            # datetime — without this, the compare below raises TypeError
            # which the except silently swallows and an expired share stays
            # accessible. Treat naive timestamps as UTC.
            if _exp.tzinfo is None:
                _exp = _exp.replace(tzinfo=timezone.utc)
            if _exp <= datetime.now(timezone.utc):
                return None
        except (ValueError, TypeError):
            pass
    try:
        snapshot = json.loads(row["snapshot"])
        if not isinstance(snapshot, dict):
            snapshot = None
    except (json.JSONDecodeError, TypeError):
        snapshot = None
    # A corrupted snapshot would have rendered as an empty share with
    # HTTP 200, which is worse than a clean 404 — the visitor sees a
    # broken page they can't tell apart from "no leads found yet".
    if snapshot is None:
        return None
    # Stability fix (audit wave 26): the previous version unconditionally
    # bumped view_count + 1 here, on every read — including the OG SVG
    # endpoint hit by every Slack/Twitter/Discord/LinkedIn unfurl bot
    # and the JSON endpoint hit by every CLI `--from-share` poll. The
    # HTML handler ALSO bot-filtered into a separate share_views table,
    # so the two counters diverged: hunt_shares.view_count was inflated
    # by bot/script reads, share_views was bot-filtered. The displayed
    # count returned to the caller was also stale (computed as
    # `row.view_count + 1` from the SELECT, not the UPDATE result, so
    # two concurrent visitors could each return 11 for what ended up as
    # 12 in the DB). Split the bump out — readers no longer mutate; the
    # caller (HTML handler) calls bump_share_view() explicitly only
    # after its own bot-UA filter, so the two counters stay aligned.
    return {
        "slug": row["slug"],
        "user_id": row["user_id"],
        "run_id": row["run_id"],
        "title": row.get("title") or "",
        "leads": snapshot.get("leads") or [],
        "meta": snapshot.get("meta") or {},
        "view_count": int(row.get("view_count") or 0),
        "created_at": row["created_at"],
    }


async def bump_share_view(slug: str) -> None:
    """Increment hunt_shares.view_count by 1 for the given slug.

    Split out from `get_hunt_share` so callers (e.g. /h/<slug>/og.svg
    serving social-unfurl bots, /h/<slug>.json answering CLI polls)
    don't inflate the counter. The HTML handler calls this explicitly
    after its existing bot-UA filter so the column stays in sync with
    the bot-filtered share_views table. Soft-fails — analytics are
    not worth crashing a public render."""
    try:
        await _aexec(
            "UPDATE hunt_shares SET view_count = view_count + 1 WHERE slug = %s",
            [slug])
    except Exception:
        pass


async def revoke_hunt_share(user_id: int, slug: str) -> bool:
    """Owner-only revoke. Returns True if a row was flipped."""
    n = await _aexec_rowcount(
        "UPDATE hunt_shares SET revoked = 1 WHERE slug = %s AND user_id = %s AND revoked = 0",
        [slug, user_id])
    return n > 0


async def list_hunt_shares(user_id: int, limit: int = 50) -> list:
    """Owner's recent shares — for an account-page management list."""
    return await _afetchall(
        "SELECT slug, run_id, title, revoked, view_count, created_at, expires_at "
        "FROM hunt_shares WHERE user_id = %s ORDER BY created_at DESC LIMIT %s",
        [user_id, max(1, min(limit, 200))]) or []


# ── Hunt recipes (Phase 11 — round-67 Tab 0) ───────────────────────


async def save_hunt_recipe(user_id: int, name: str, config: dict,
                            description: str = "") -> int:
    """Upsert a named hunt recipe. Returns recipe id.

    `config` is a dict with the fields cmd_hunt accepts: countries,
    max_leads, queries, etc. Persisted as JSON so future format
    extensions don't need a schema change.
    """
    name_clean = (name or "").strip()
    if not name_clean:
        raise ValueError("recipe name required")
    now = datetime.now(timezone.utc).isoformat()
    cfg_json = json.dumps(config or {}, ensure_ascii=False, default=str)
    desc = (description or "")[:500]
    # Try insert first; if a recipe with this user+name exists, update
    # instead. SQLite supports ON CONFLICT DO UPDATE since 3.24+ which
    # is well below our 3.35 floor; same for Postgres.
    row = await _afetchone(
        "INSERT INTO hunt_recipes "
        "(user_id, name, config_json, description, created_at, updated_at, run_count) "
        "VALUES (%s, %s, %s, %s, %s, %s, 0) "
        "ON CONFLICT (user_id, name) DO UPDATE SET "
        "config_json = EXCLUDED.config_json, "
        "description = EXCLUDED.description, "
        "updated_at = EXCLUDED.updated_at "
        "RETURNING id",
        [user_id, name_clean, cfg_json, desc, now, now])
    if row and "id" in row:
        return int(row["id"])
    return 0


async def get_hunt_recipe(user_id: int, name: str) -> dict | None:
    """Fetch a recipe by name. Returns dict with parsed config or None."""
    row = await _afetchone(
        "SELECT id, name, config_json, description, last_run_at, run_count, "
        "created_at, updated_at FROM hunt_recipes WHERE user_id = %s AND name = %s",
        [user_id, (name or "").strip()])
    if not row:
        return None
    try:
        cfg = json.loads(row.get("config_json") or "{}")
        if not isinstance(cfg, dict):
            cfg = {}
    except (json.JSONDecodeError, TypeError):
        cfg = {}
    out = dict(row)
    out["config"] = cfg
    return out


async def list_hunt_recipes(user_id: int, limit: int = 100) -> list:
    """All recipes for a user, most recently updated first."""
    rows = await _afetchall(
        "SELECT id, name, description, last_run_at, run_count, created_at, updated_at "
        "FROM hunt_recipes WHERE user_id = %s ORDER BY updated_at DESC LIMIT %s",
        [user_id, max(1, min(limit, 500))])
    return [dict(r) for r in (rows or [])]


async def get_recipe_last_lead_ids(user_id: int, name: str) -> list[str]:
    """Lead-ids the recipe collected on its previous run. Used to
    compute the new-vs-stale diff when the recipe replays."""
    row = await _afetchone(
        "SELECT last_run_lead_ids FROM hunt_recipes WHERE user_id = %s AND name = %s",
        [user_id, (name or "").strip()])
    if not row:
        return []
    try:
        ids = json.loads(row.get("last_run_lead_ids") or "[]")
        return [str(x) for x in ids] if isinstance(ids, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


async def set_recipe_last_lead_ids(user_id: int, name: str, lead_ids: list[str]) -> None:
    """Snapshot the lead-id set this recipe just produced so the next
    replay can diff against it."""
    payload = json.dumps([str(x) for x in (lead_ids or [])][:1000], ensure_ascii=False)
    await _aexec(
        "UPDATE hunt_recipes SET last_run_lead_ids = %s WHERE user_id = %s AND name = %s",
        [payload, user_id, (name or "").strip()])


async def publish_public_recipe(payload: dict, name: str = "",
                                 description: str = "") -> str:
    """Persist a public recipe and return its slug. Slug uniqueness is
    enforced by the column UNIQUE constraint — collisions get a fresh
    slug (very rare, since we use 8-char hex). Kimi round-74 scaffold;
    gated at the route layer by HV_RECIPE_URL_BETA pre-launch."""
    import secrets as _sec
    body = json.dumps(payload or {}, ensure_ascii=False, default=str)
    now = datetime.now(timezone.utc).isoformat()
    for _ in range(4):  # try up to 4 times if a slug collides
        slug = _sec.token_hex(4)
        try:
            await _aexec(
                "INSERT INTO public_recipes (slug, payload, name, description, created_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                [slug, body, (name or "")[:120], (description or "")[:400], now])
            return slug
        except Exception:
            continue
    raise RuntimeError("could not mint a public recipe slug after 4 attempts")


async def get_public_recipe(slug: str) -> dict:
    """Fetch a public recipe by slug. Returns {} if not found or
    revoked. Soft-revoke via revoked_at IS NOT NULL."""
    row = await _afetchone(
        "SELECT slug, payload, name, description, created_at, revoked_at "
        "FROM public_recipes WHERE slug = %s",
        [(slug or "").strip()])
    if not row or row.get("revoked_at"):
        return {}
    try:
        payload = json.loads(row.get("payload") or "{}")
        if not isinstance(payload, dict):
            payload = {}
    except (json.JSONDecodeError, TypeError):
        payload = {}
    return {
        "slug": row["slug"],
        "payload": payload,
        "name": row.get("name") or "",
        "description": row.get("description") or "",
        "created_at": row.get("created_at") or "",
    }


async def record_share_view(slug: str, ip_hash: str = "",
                             user_agent: str = "", referrer: str = "") -> None:
    """Record one /h/<slug> page view. De-dup by ip_hash + slug per hour."""
    if not slug:
        return
    try:
        recent = await _afetchone(
            "SELECT id FROM share_views WHERE slug = %s AND ip_hash = %s "
            "AND viewed_at > datetime('now', '-1 hour') LIMIT 1"
            if _is_sqlite() else
            "SELECT id FROM share_views WHERE slug = %s AND ip_hash = %s "
            "AND viewed_at > NOW() - INTERVAL '1 hour' LIMIT 1",
            [slug, ip_hash])
        if recent:
            return
        now = datetime.now(timezone.utc).isoformat()
        await _aexec(
            "INSERT INTO share_views (slug, ip_hash, user_agent, referrer, viewed_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            [slug, (ip_hash or "")[:32], (user_agent or "")[:200],
             (referrer or "")[:400], now])
    except Exception:
        pass  # never crash the page render on telemetry failure


async def get_share_view_count(slug: str, days: int = 30) -> int:
    """Total unique-by-hour views in the last N days."""
    if not slug:
        return 0
    try:
        if _is_sqlite():
            row = await _afetchone(
                "SELECT COUNT(*) AS n FROM share_views WHERE slug = %s "
                "AND viewed_at > datetime('now', %s)",
                [slug, f"-{int(days)} days"])
        else:
            row = await _afetchone(
                "SELECT COUNT(*) AS n FROM share_views WHERE slug = %s "
                "AND viewed_at > NOW() - (%s || ' days')::INTERVAL",
                [slug, str(int(days))])
        return int((row or {}).get("n", 0))
    except Exception:
        return 0


async def mint_cloud_proxy_token(user_email: str = "", plan: str = "design_partner",
                                  daily_quota: int = 200,
                                  expires_at: str | None = None,
                                  notes: str = "") -> str:
    """Mint a Cloud Proxy access token. Admin-only — call from
    `huntova cloud token mint <email>`."""
    import secrets as _sec
    now = datetime.now(timezone.utc).isoformat()
    for _ in range(4):
        token = "hcp_" + _sec.token_urlsafe(24)
        try:
            await _aexec(
                "INSERT INTO cloud_proxy_tokens "
                "(token, user_email, plan, daily_quota, last_reset_date, "
                "expires_at, created_at, notes) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                [token, (user_email or "")[:200], (plan or "design_partner")[:32],
                 max(1, int(daily_quota or 200)),
                 datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                 expires_at, now, (notes or "")[:400]])
            return token
        except Exception:
            continue
    raise RuntimeError("could not mint a unique cloud-proxy token")


async def get_cloud_proxy_token(token: str) -> dict:
    """Fetch one token row. Returns {} if not found/revoked/expired."""
    if not token or not token.startswith("hcp_"):
        return {}
    row = await _afetchone(
        "SELECT id, token, user_email, plan, daily_quota, used_today, "
        "last_reset_date, expires_at, revoked_at "
        "FROM cloud_proxy_tokens WHERE token = %s",
        [token])
    if not row or row.get("revoked_at"):
        return {}
    expires = row.get("expires_at")
    if expires:
        try:
            if datetime.fromisoformat(str(expires).replace("Z", "+00:00")) < datetime.now(timezone.utc):
                return {}
        except (ValueError, TypeError):
            pass
    return dict(row)


async def consume_cloud_proxy_quota(token: str) -> tuple[bool, int]:
    """Atomically increment used_today, reset on UTC date rollover.
    Returns (allowed, remaining)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = await get_cloud_proxy_token(token)
    if not row:
        return False, 0
    quota = int(row.get("daily_quota") or 0)
    used = int(row.get("used_today") or 0)
    last_reset = (row.get("last_reset_date") or "").strip()
    if last_reset != today:
        await _aexec(
            "UPDATE cloud_proxy_tokens SET used_today = 0, last_reset_date = %s "
            "WHERE token = %s",
            [today, token])
        used = 0
    if used >= quota:
        return False, 0
    await _aexec(
        "UPDATE cloud_proxy_tokens SET used_today = used_today + 1 WHERE token = %s",
        [token])
    return True, max(0, quota - used - 1)


async def record_metric(event: str, platform: str = "", version: str = "",
                         props: dict | None = None) -> None:
    """Append one opt-in telemetry event to the metrics table. Soft-
    fails on any error so a metrics outage never breaks the request /
    CLI flow. Schema is intentionally tiny — no FK, no per-user join."""
    try:
        payload = json.dumps(props or {}, ensure_ascii=False, default=str)
        now = datetime.now(timezone.utc).isoformat()
        await _aexec(
            "INSERT INTO metrics (event, platform, version, props, ts) "
            "VALUES (%s, %s, %s, %s, %s)",
            [(event or "")[:32], (platform or "")[:32], (version or "")[:32],
             payload, now])
    except Exception:
        pass


async def save_recipe_adaptation(user_id: int, name: str, adaptation: dict) -> None:
    """Persist the AI-generated adaptation card on a recipe."""
    payload = json.dumps(adaptation or {}, ensure_ascii=False, default=str)
    now = datetime.now(timezone.utc).isoformat()
    await _aexec(
        "UPDATE hunt_recipes SET adaptation_json = %s, adaptation_at = %s "
        "WHERE user_id = %s AND name = %s",
        [payload, now, user_id, (name or "").strip()])


async def get_recipe_adaptation(user_id: int, name: str) -> dict:
    """Fetch the most recent adaptation card. Empty dict if none."""
    row = await _afetchone(
        "SELECT adaptation_json, adaptation_at FROM hunt_recipes "
        "WHERE user_id = %s AND name = %s",
        [user_id, (name or "").strip()])
    if not row:
        return {}
    try:
        data = json.loads(row.get("adaptation_json") or "{}")
        if not isinstance(data, dict):
            data = {}
    except (json.JSONDecodeError, TypeError):
        data = {}
    if row.get("adaptation_at"):
        data["adaptation_at"] = row["adaptation_at"]
    return data


async def get_recipe_outcomes(user_id: int, name: str) -> dict:
    """Aggregate the lead_feedback + email_status of leads produced
    by a recipe's most recent run. Returns:

        {
            "lead_ids": [...],
            "total": N,
            "feedback": {"good": x, "bad": y, "none": z},
            "status": {"new": ..., "email_sent": ..., "replied": ..., ...},
            "fit_band": {"high": x (>=8), "medium": y (5-7), "low": z (<5)},
            "reply_rate_pct": int,
        }

    Read-only — built from existing tables, no schema change.
    Foundation for the v2.0 outcome-trained recipe DNA per Tab 0
    round-69. Future iterations layer adaptation summaries on top.
    """
    lead_ids = await get_recipe_last_lead_ids(user_id, name)
    out = {
        "lead_ids": lead_ids,
        "total": len(lead_ids),
        "feedback": {"good": 0, "bad": 0, "none": 0},
        "status": {},
        "fit_band": {"high": 0, "medium": 0, "low": 0},
        "reply_rate_pct": 0,
    }
    if not lead_ids:
        return out
    # Pull the leads — bounded by how many ids we have.
    placeholders = ", ".join(["%s"] * len(lead_ids))
    rows = await _afetchall(
        f"SELECT lead_id, data, email_status, fit_score FROM leads "
        f"WHERE user_id = %s AND lead_id IN ({placeholders})",
        [user_id] + list(lead_ids))
    rows = rows or []
    sent_set = ("email_sent", "followed_up", "replied", "meeting_booked", "won")
    replied_set = ("replied", "meeting_booked", "won")
    sent_n = 0
    replied_n = 0
    for r in rows:
        es = (r.get("email_status") or "new")
        out["status"][es] = out["status"].get(es, 0) + 1
        if es in sent_set:
            sent_n += 1
        if es in replied_set:
            replied_n += 1
        try:
            f = int(r.get("fit_score") or 0)
        except Exception:
            f = 0
        if f >= 8:
            out["fit_band"]["high"] += 1
        elif f >= 5:
            out["fit_band"]["medium"] += 1
        else:
            out["fit_band"]["low"] += 1
    # Lead feedback counts
    fb_rows = await _afetchall(
        f"SELECT signal, COUNT(*) AS n FROM lead_feedback WHERE user_id = %s "
        f"AND lead_id IN ({placeholders}) GROUP BY signal",
        [user_id] + list(lead_ids))
    for r in (fb_rows or []):
        sig = (r.get("signal") or "").lower()
        if sig == "good":
            out["feedback"]["good"] = int(r.get("n") or 0)
        elif sig == "bad":
            out["feedback"]["bad"] = int(r.get("n") or 0)
    out["feedback"]["none"] = max(0, out["total"] - out["feedback"]["good"] - out["feedback"]["bad"])
    out["reply_rate_pct"] = round(100 * replied_n / sent_n) if sent_n else 0
    out["sent_n"] = sent_n
    out["replied_n"] = replied_n
    return out


async def delete_hunt_recipe(user_id: int, name: str) -> bool:
    """Remove a recipe by name. Returns True if it existed."""
    n = await _aexec_rowcount(
        "DELETE FROM hunt_recipes WHERE user_id = %s AND name = %s",
        [user_id, (name or "").strip()])
    return n > 0


async def touch_hunt_recipe(user_id: int, name: str) -> None:
    """Bump last_run_at + run_count when a recipe is replayed."""
    now = datetime.now(timezone.utc).isoformat()
    await _aexec(
        "UPDATE hunt_recipes SET last_run_at = %s, run_count = run_count + 1 "
        "WHERE user_id = %s AND name = %s",
        [now, user_id, (name or "").strip()])


# ── Growth analytics (Feature F6) ──
# These helpers feed the admin /api/ops/metrics endpoint. Kept thin —
# raw counts only, no time-series. The win is decision support, not BI.

async def record_checkout_start(user_id: int, product_id: str, source: str = ""):
    """Capture the click on any "Buy" or "Upgrade" CTA, regardless of
    whether the user finishes Stripe checkout. Compared to
    `stripe_events`, this captures intent + which paywall surface drove
    it — completed-only data hides the best signal.
    """
    now = datetime.now(timezone.utc).isoformat()
    await _aexec(
        "INSERT INTO checkout_starts (user_id, product_id, source, created_at) "
        "VALUES (%s, %s, %s, %s)",
        [user_id, (product_id or "")[:60], (source or "")[:60], now])


async def get_growth_metrics(days: int = 7) -> dict:
    """One-shot growth dashboard read. Counts in the last N days for:
    hunts completed, shares created, share views, leads marked
    sent/replied. ISO 8601 string compares are safe here because all
    timestamps are stored in UTC isoformat().
    """
    since = (datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 90)))).isoformat()
    out: dict = {"days": days, "since": since}

    # Hunts completed in window — 'finished' is the natural-completion
    # status; 'stopped' / 'error' are deliberately excluded.
    row = await _afetchone(
        "SELECT COUNT(*) AS n FROM agent_runs WHERE status IN ('finished','stopped') AND ended_at >= %s",
        [since])
    out["hunts_completed"] = int(row["n"]) if row else 0

    # Hunt shares created in window.
    row = await _afetchone(
        "SELECT COUNT(*) AS n FROM hunt_shares WHERE created_at >= %s AND revoked = 0",
        [since])
    out["shares_created"] = int(row["n"]) if row else 0
    # Share views (cumulative — view_count isn't per-window; fold the
    # active shares' totals together as a coarse activity signal).
    row = await _afetchone(
        "SELECT COALESCE(SUM(view_count), 0) AS n FROM hunt_shares WHERE created_at >= %s AND revoked = 0",
        [since])
    out["share_views"] = int(row["n"]) if row else 0

    # Lead-action funnel: count distinct leads moved into each status
    # band in the window. Uses created_at on lead_actions which gets a
    # row each time email_status flips via /api/track-actions.
    sent_set = ("email_sent", "followed_up", "replied", "meeting_booked", "won")
    replied_set = ("replied", "meeting_booked", "won")
    rows = await _afetchall(
        "SELECT action_type, COUNT(*) AS n FROM lead_actions WHERE created_at >= %s "
        "AND action_type IN ('email_sent','email_followed_up','email_replied') GROUP BY action_type",
        [since])
    by_action = {(r["action_type"] or ""): int(r["n"] or 0) for r in (rows or [])}
    out["leads_marked_sent"] = by_action.get("email_sent", 0)
    out["leads_marked_replied"] = by_action.get("email_replied", 0)
    # Reply rate (replied / sent) — guard against div-by-zero, render
    # as percentage int. Useful KPI for outreach quality.
    sent_n = out["leads_marked_sent"] or 0
    replied_n = out["leads_marked_replied"] or 0
    out["reply_rate_pct"] = round(100 * replied_n / sent_n) if sent_n else 0

    # New users in window — context for everything else.
    row = await _afetchone(
        "SELECT COUNT(*) AS n FROM users WHERE created_at >= %s",
        [since])
    out["new_users"] = int(row["n"]) if row else 0
    return out


async def get_checkout_source_metrics(days: int = 30, limit: int = 30) -> list:
    """Checkout starts grouped by (source, product_id) in the window.
    Surfaces which paywall surface (credits_exhausted vs
    credits_exhausted_start_popup vs '') drives clicks for which
    product. Empty source bucket is the legacy direct-call path.
    """
    since = (datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 365)))).isoformat()
    rows = await _afetchall(
        "SELECT COALESCE(source,'') AS source, product_id, COUNT(*) AS clicks "
        "FROM checkout_starts WHERE created_at >= %s "
        "GROUP BY source, product_id ORDER BY clicks DESC LIMIT %s",
        [since, max(1, min(limit, 200))])
    return [{"source": r["source"] or "(unsourced)", "product_id": r["product_id"], "clicks": int(r["clicks"] or 0)}
            for r in (rows or [])]


# ── GDPR ──

async def gdpr_erasure(user_id: int, identifier: str) -> dict:
    leads = await get_leads(user_id)
    deleted = 0
    for lead in leads:
        match = False
        if "@" in identifier:
            email = identifier.lower().strip()
            if (lead.get("contact_email") or "").lower() == email:
                match = True
        else:
            from urllib.parse import urlparse
            domain = identifier.lower().replace("www.", "").strip()
            for field in ("org_website", "url"):
                try:
                    d = urlparse(lead.get(field, "")).netloc.lower().replace("www.", "")
                    if d == domain:
                        match = True
                except Exception:
                    pass
        if match:
            await permanent_delete_lead(user_id, lead["lead_id"])
            deleted += 1
    return {"deleted": deleted, "remaining": len(leads) - deleted}
