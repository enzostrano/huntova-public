"""SQLite driver shim — translation rules + basic CRUD."""
from __future__ import annotations


def test_sqlite_driver_selected_in_local_mode(local_env):
    from db_driver import get_driver
    drv = get_driver()
    assert drv.name == "sqlite"
    assert drv.placeholder == "?"


def test_sql_translation_serial_to_autoincrement():
    from db_driver import _pg_to_sqlite
    pg = "CREATE TABLE x (id SERIAL PRIMARY KEY, name TEXT)"
    out = _pg_to_sqlite(pg)
    assert "INTEGER PRIMARY KEY AUTOINCREMENT" in out
    assert "SERIAL" not in out


def test_sql_translation_placeholder():
    from db_driver import _pg_to_sqlite
    assert _pg_to_sqlite("WHERE id = %s") == "WHERE id = ?"
    # %% (literal %) should NOT be translated
    assert _pg_to_sqlite("WHERE pct LIKE '50%%'") == "WHERE pct LIKE '50%%'"


def test_sql_translation_for_update_stripped():
    from db_driver import _pg_to_sqlite
    assert "FOR UPDATE" not in _pg_to_sqlite("SELECT * FROM x FOR UPDATE")


def test_sql_translation_xmax_replaced():
    from db_driver import _pg_to_sqlite
    out = _pg_to_sqlite("RETURNING (xmax = 0) AS was_inserted")
    assert "1 AS was_inserted" in out
    assert "xmax" not in out


def test_local_db_path_respects_explicit_override(local_env):
    """HUNTOVA_DB_PATH wins over XDG resolution."""
    from db_driver import _local_db_path
    p = _local_db_path()
    # Fixture sets HUNTOVA_DB_PATH explicitly, so that's what we get back.
    assert str(p) == str(local_env["db_path"])


def test_local_db_path_falls_back_to_xdg(tmp_path, monkeypatch):
    """When HUNTOVA_DB_PATH is unset, use XDG_DATA_HOME / huntova / db.sqlite."""
    monkeypatch.delenv("HUNTOVA_DB_PATH", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    import importlib
    import db_driver
    importlib.reload(db_driver)
    try:
        p = db_driver._local_db_path()
        assert "huntova" in str(p)
        assert str(tmp_path) in str(p)
        assert p.name == "db.sqlite"
    finally:
        # Reset the module so subsequent tests pick up their own
        # fixture-provided HUNTOVA_DB_PATH cleanly.
        importlib.reload(db_driver)
        # db.py caches the driver at module load — reload it too.
        if "db" in importlib.sys.modules:
            importlib.reload(importlib.sys.modules["db"])


def test_init_schema_creates_users_table(local_env):
    import db
    db.init_db_sync()
    # Query through the driver to confirm the schema applied.
    from db_driver import get_driver
    drv = get_driver()
    conn = drv.get_conn()
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
    row = cur.fetchone()
    assert row is not None
