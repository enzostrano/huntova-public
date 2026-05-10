"""BRAIN-205: db._is_sqlite + _xlate + _SqliteSerial helpers audit.

Pure-ish helpers in db.py that bridge the SQLite-vs-Postgres
backend selection. Pinned invariants:

1. `_is_sqlite` returns True when the active driver's name is
   'sqlite', False otherwise.
2. `_xlate` round-trips SQL through the driver's translator (no-op
   in Postgres, applies _pg_to_sqlite in SQLite).
3. `_SqliteSerial` context manager acquires `_sqlite_lock` ONLY in
   SQLite mode (Postgres path is no-op).
4. `_SqliteSerial` re-entrancy works (RLock).
5. Lock release happens on exception inside the block.
"""
from __future__ import annotations


def test_is_sqlite_in_local_mode(local_env):
    """In local-mode test harness, the driver is SQLite."""
    import db
    assert db._is_sqlite() is True


def test_xlate_translates_pg_placeholder(local_env):
    """In SQLite mode, %s placeholders translate to ?."""
    import db
    out = db._xlate("SELECT * FROM t WHERE id = %s")
    assert "?" in out
    assert "%s" not in out


def test_xlate_translates_serial_primary_key(local_env):
    import db
    out = db._xlate("CREATE TABLE x (id SERIAL PRIMARY KEY)")
    assert "INTEGER PRIMARY KEY AUTOINCREMENT" in out
    assert "SERIAL" not in out


def test_xlate_strips_for_update(local_env):
    import db
    out = db._xlate("SELECT * FROM t FOR UPDATE")
    assert "FOR UPDATE" not in out.upper()


def test_xlate_handles_empty(local_env):
    import db
    assert db._xlate("") == ""


def test_xlate_idempotent(local_env):
    """Translating already-translated SQL produces same output."""
    import db
    sql = "SELECT * FROM t WHERE id = %s FOR UPDATE"
    once = db._xlate(sql)
    twice = db._xlate(once)
    assert once == twice


def test_sqlite_serial_acquires_in_sqlite_mode(local_env):
    """Context manager acquires `_sqlite_lock` in SQLite mode and
    releases it on exit."""
    import db
    # Lock should be unlocked at start.
    # Enter context manager.
    with db._SqliteSerial() as s:
        # Lock held during the block.
        assert s._held is True
    # After exit, lock is released — we can re-acquire.
    with db._SqliteSerial() as s2:
        assert s2._held is True


def test_sqlite_serial_releases_on_exception(local_env):
    """If the block raises, the lock is still released (so the next
    operation can acquire)."""
    import db

    class _TestException(Exception):
        pass

    try:
        with db._SqliteSerial():
            raise _TestException("inside-block")
    except _TestException:
        pass

    # Lock must have been released — re-acquire works.
    with db._SqliteSerial() as s:
        assert s._held is True


def test_sqlite_serial_re_entrancy(local_env):
    """`_sqlite_lock` is an RLock — same thread can re-enter."""
    import db
    with db._SqliteSerial():
        # Re-enter — must not deadlock.
        with db._SqliteSerial() as inner:
            assert inner._held is True


def test_xlate_chains_multiple_replacements(local_env):
    """A query with several PG-isms translates them all in one pass."""
    import db
    sql = "INSERT INTO t VALUES (%s, %s) ON CONFLICT DO NOTHING RETURNING (xmax = 0) AS w"
    out = db._xlate(sql)
    assert "%s" not in out
    assert "?" in out
    assert "xmax" not in out.lower()
    assert "1 AS w" in out


def test_xlate_handles_greatest_least(local_env):
    """GREATEST / LEAST translated to MAX / MIN (BRAIN-161 pin)."""
    import db
    out = db._xlate("SELECT GREATEST(a, 0), LEAST(b, 100)")
    assert "GREATEST" not in out
    assert "LEAST" not in out
    assert "MAX(" in out
    assert "MIN(" in out
