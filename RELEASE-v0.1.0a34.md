# Huntova v0.1.0a34 — 2026-05-01

**Real concurrency bug crushed.** SQLite local mode was racing on a
shared connection under parallel /api/* requests, producing
intermittent `sqlite3.InterfaceError: bad parameter or other API
misuse` 500s + a separate UNIQUE-constraint race on the local-user
bootstrap, both of which manifested user-side as random "page bounces
to /landing" on first paint of the dashboard. This release fixes
both. **100/100 parallel-request stress test passes clean.**

## Bug fixes

### SQLite single-connection concurrency
- `db_driver._SQLiteDriver` returns one shared `sqlite3.Connection`
  with `check_same_thread=False`. The intent was that the driver's
  `_lock` would serialise writes, but `db.py`'s helpers
  (`_fetchone`, `_fetchall`, `_exec`, ...) didn't acquire it.
- Multiple `asyncio.to_thread(_fetchone, ...)` calls running in the
  ThreadPoolExecutor would call `cursor.execute()` simultaneously
  on the same connection → SQLite raises `bad parameter or other
  API misuse`.
- Added a module-level RLock (`_sqlite_lock`) + `_SqliteSerial`
  context manager. All six helpers (`_exec`, `_fetchone`,
  `_fetchall`, `_exec_returning`, `_exec_rowcount`,
  `_exec_pipeline`) now wrap their bodies in `with _SqliteSerial():`
  which is a no-op in cloud/Postgres mode.
- Cost: SQLite local mode is single-user anyway — concurrent
  fetches block briefly on this lock. Negligible vs the actual
  disk I/O.

### Local-user bootstrap UNIQUE-race
- `_ensure_local_user()` could be called concurrently on first
  paint (10+ parallel /api/* fetches). Each call would
  `get_user_by_email("local@huntova.app")`, see no user, and try
  to `create_user(...)`. The second insert tripped the UNIQUE
  constraint → exception → `get_current_user` returned None →
  page bounced to /landing.
- Wrapped `_ensure_local_user` in an `asyncio.Lock`. First waiter
  creates the row; subsequent waiters take the fast path. Plus a
  belt-and-braces re-fetch on `create_user` failure (in case the
  lock is somehow bypassed by a pre-existing row).

## Stress test (verified live)
- ✓ `for i in 1..20; curl /api/account &; wait` → 20/20 200s (was
  20/20 mixed 500/401/200 before fix)
- ✓ `for i in 1..50; curl /api/account &; wait` → 50/50 200s
- ✓ Mixed 25× `/api/account` + 25× `/api/leads` parallel → 50/50
  200s
- ✓ No `sqlite3.InterfaceError` in server log post-fix
- ✓ No `/landing` redirect in Playwright after fix lands

## Updates
- None.

## Known issues
- Cloud-side `telemetry_opt_in` flag still not consulted (carry-over).
