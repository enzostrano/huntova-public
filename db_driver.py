"""
Database driver shim — backend selection for cloud (PostgreSQL) vs
local (SQLite). Lets db.py keep its 1900 lines of raw SQL with `%s`
placeholders + RealDictCursor while still running against SQLite when
the user runs `huntova serve` on their machine.

Selection rules:
  - APP_MODE=local AND no DATABASE_URL  → SQLite at the local default path
  - DATABASE_URL set                    → PostgreSQL via psycopg2
  - APP_MODE=local AND DATABASE_URL set → PostgreSQL (advanced override)

The shim is intentionally thin. db.py imports `get_driver()` and uses:
  - driver.placeholder         — "%s" or "?"
  - driver.get_conn()          — pool checkout
  - driver.put_conn(conn)      — pool return (or close, for SQLite)
  - driver.cursor_factory      — RealDictCursor or sqlite3 row-as-dict
  - driver.translate_sql(sql)  — best-effort %s→? + Postgres→SQLite shim
  - driver.exec_returning(cur, sql, params) — handles RETURNING shape diff
  - driver.init_schema(sql)    — runs the SCHEMA_SQL block

Everything else (transaction semantics, ON CONFLICT, JSON-as-TEXT)
already happens to be portable enough that the existing SQL works on
both backends after `translate_sql`.
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any, Callable

# psycopg2 is imported lazily — local-mode installs may not have it
# available (or want it as an optional dep down the line).
_pg_pool = None
_pg_RealDictCursor = None


# ── Path resolution for the local SQLite database ──────────────────

def _local_db_path() -> Path:
    override = os.environ.get("HUNTOVA_DB_PATH")
    if override:
        p = Path(override).expanduser()
    else:
        # XDG data dir convention. Mirrors how the CLI stores config in
        # ~/.config/huntova/. Per engineering round 65 + Gemini's plan,
        # secrets live in ~/.config/huntova/, mutable data lives in
        # ~/.local/share/huntova/.
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        p = Path(base) / "huntova" / "db.sqlite"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


# ── SQL translation: PostgreSQL → SQLite ───────────────────────────

# Compiled once. Translation is best-effort; queries are scanned in the
# order below and the first match wins on each line. Idempotent in
# Postgres mode (translate_sql is a no-op there).
_RE_SERIAL_PRIMARY_KEY = re.compile(r"\bSERIAL\s+PRIMARY\s+KEY\b", re.IGNORECASE)
_RE_FOR_UPDATE = re.compile(r"\bFOR\s+UPDATE\b", re.IGNORECASE)
# `(xmax = 0) AS was_inserted` returns whether the row was newly
# inserted vs updated by ON CONFLICT in PostgreSQL. SQLite's UPSERT
# doesn't expose this; we replace with a `1 AS was_inserted` so the
# return shape stays the same. Code that relies on the flag will see
# every UPSERT as an insert in local mode — that's an acceptable
# small loss (the flag drives credit-deduction-on-rediscovery, which
# doesn't apply locally because billing is disabled).
_RE_XMAX_INSERTED = re.compile(r"\(\s*xmax\s*=\s*0\s*\)\s+AS\s+(\w+)", re.IGNORECASE)
# %s positional placeholders. Naive translation: skip %% (literal %)
# and %(name)s named placeholders (SQLite uses :name).
_RE_PG_PLACEHOLDER = re.compile(r"(?<!%)%s")
# CREATE INDEX IF NOT EXISTS — both support this since PG 9.5 + SQLite 3.0.
# SERIAL/RETURNING/ON CONFLICT all already supported in SQLite ≥ 3.35.

# GREATEST/LEAST: SQLite doesn't expose them as scalar functions. The
# 2-argument MAX/MIN scalar variants behave identically when both args
# are numeric (the SQL functions are scalar in SQLite when given >= 2
# arguments — distinct from MAX/MIN as aggregates with one column).
# Without this translation `db.py:apply_credit_delta` and the admin
# revoke path crash on SQLite with `no such function: GREATEST`.
_RE_GREATEST = re.compile(r"\bGREATEST\s*\(", re.IGNORECASE)
_RE_LEAST = re.compile(r"\bLEAST\s*\(", re.IGNORECASE)


def _pg_to_sqlite(sql: str) -> str:
    if not sql:
        return sql
    out = sql
    out = _RE_SERIAL_PRIMARY_KEY.sub("INTEGER PRIMARY KEY AUTOINCREMENT", out)
    out = _RE_FOR_UPDATE.sub("", out)
    out = _RE_XMAX_INSERTED.sub(r"1 AS \1", out)
    out = _RE_GREATEST.sub("MAX(", out)
    out = _RE_LEAST.sub("MIN(", out)
    out = _RE_PG_PLACEHOLDER.sub("?", out)
    return out


# ── Driver classes ─────────────────────────────────────────────────


class _PostgresDriver:
    name = "postgres"
    placeholder = "%s"

    def __init__(self, dsn: str, minconn: int = 2, maxconn: int = 10):
        global _pg_pool, _pg_RealDictCursor
        import psycopg2  # noqa: F401  — only needed when this driver is selected
        import psycopg2.extras
        from psycopg2.pool import ThreadedConnectionPool
        _pg_RealDictCursor = psycopg2.extras.RealDictCursor
        _pg_pool = ThreadedConnectionPool(minconn=minconn, maxconn=maxconn, dsn=dsn)
        self._pool = _pg_pool
        self.cursor_factory = _pg_RealDictCursor

    def get_conn(self):
        return self._pool.getconn()

    def put_conn(self, conn):
        # Sanitisation logic lives in db.py:put_conn — we just hand back.
        try:
            self._pool.putconn(conn)
        except Exception:
            try:
                self._pool.putconn(conn, close=True)
            except Exception:
                pass

    def translate_sql(self, sql: str) -> str:
        # Postgres native — pass through unchanged.
        return sql

    def init_schema(self, schema_sql: str) -> None:
        conn = self.get_conn()
        try:
            cur = conn.cursor()
            cur.execute(schema_sql)
            conn.commit()
        finally:
            self.put_conn(conn)


class _SQLiteRowFactory:
    """Returned objects support both dict-style and key-name access so
    the existing `row["foo"]` calls in db.py work unchanged."""

    @staticmethod
    def factory(cursor, row):
        # Build a dict keyed by column name. Slightly more memory than
        # sqlite3.Row but lets `row.get("x")` work without rewriting
        # callers.
        return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}


class _SQLiteDriver:
    name = "sqlite"
    placeholder = "?"
    cursor_factory = None  # SQLite uses row_factory on the connection, not cursor.

    def __init__(self, db_path: Path):
        self._path = db_path
        self._lock = threading.RLock()
        # Single-writer DB: keep one shared connection (with check_same_thread
        # off) and serialise writes through the lock. Reads are still
        # concurrent thanks to SQLite's WAL mode.
        self._conn = sqlite3.connect(
            str(db_path),
            check_same_thread=False,
            isolation_level=None,  # autocommit; explicit BEGIN/COMMIT in callers
        )
        self._conn.row_factory = _SQLiteRowFactory.factory
        # Performance + reliability tuning. WAL is the standard
        # recommendation for any non-trivial SQLite workload.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        # a323 SECURITY FIX: tighten file permissions to 0600 so other
        # OS users on the same machine can't read leads, ICP answers,
        # chat messages, or session cookies. The chmod was only being
        # done during `huntova init` / `huntova onboard`, but users
        # who skip onboarding and go straight to `huntova serve` had
        # their DB at the default umask (0644 on macOS) — world-
        # readable. Same for the WAL/SHM sidecars SQLite creates
        # alongside the main file. Idempotent + cheap; runs once per
        # connection-pool init. Skip on Windows (POSIX modes don't
        # apply).
        if os.name != "nt":
            for _suffix in ("", "-wal", "-shm", "-journal"):
                _p = Path(str(db_path) + _suffix)
                if _p.exists():
                    try:
                        os.chmod(_p, 0o600)
                    except OSError:
                        pass
            # Also tighten the parent dir to 0700 so a directory listing
            # can't enumerate file names + sizes.
            try:
                os.chmod(db_path.parent, 0o700)
            except OSError:
                pass

    def get_conn(self):
        # Fake-pool: callers always get the singleton connection.
        # Concurrency is controlled by the shared lock around
        # write-paths in db.py (and SQLite's busy_timeout for reads).
        return self._conn

    def put_conn(self, conn):
        # No-op — connection is shared, never returned to a pool.
        return None

    def translate_sql(self, sql: str) -> str:
        return _pg_to_sqlite(sql)

    def init_schema(self, schema_sql: str) -> None:
        translated = self.translate_sql(schema_sql)
        # SQLite executescript runs the whole multi-statement block in
        # one go, with implicit COMMIT at the end. Perfect for an
        # idempotent CREATE TABLE IF NOT EXISTS bundle.
        with self._lock:
            self._conn.executescript(translated)


# ── Driver singleton resolution ────────────────────────────────────

_driver: _PostgresDriver | _SQLiteDriver | None = None
_driver_lock = threading.Lock()


def get_driver():
    global _driver
    if _driver is not None:
        return _driver
    with _driver_lock:
        if _driver is not None:
            return _driver
        dsn = os.environ.get("DATABASE_URL", "").strip()
        app_mode = (os.environ.get("APP_MODE") or "cloud").strip().lower()
        if dsn:
            _driver = _PostgresDriver(dsn=dsn)
        elif app_mode == "local":
            _driver = _SQLiteDriver(db_path=_local_db_path())
        else:
            # Cloud mode without DATABASE_URL — preserve the original
            # error path. db.py's get_conn() will raise the friendly
            # message.
            raise RuntimeError(
                "DATABASE_URL not set and APP_MODE != local. "
                "Set DATABASE_URL for cloud, or APP_MODE=local for the CLI shape."
            )
        return _driver


def reset_driver_for_tests() -> None:
    """Test/CLI helper. Recreates the driver singleton on next get."""
    global _driver
    with _driver_lock:
        _driver = None
