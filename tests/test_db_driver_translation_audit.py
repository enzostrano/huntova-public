"""BRAIN-161: db_driver.py SQL-translation invariant audit.

`_pg_to_sqlite` rewrites the PostgreSQL dialect we author in db.py so
the same SQL runs against SQLite in local mode. Each rewrite is a
small regex; the suite below pins:

1. GREATEST / LEAST → MAX( / MIN( (the audit-wave fix that closed
   the apply_credit_delta crash in local mode).
2. SERIAL PRIMARY KEY → INTEGER PRIMARY KEY AUTOINCREMENT.
3. FOR UPDATE stripped (SQLite doesn't support row-level locks; the
   db_driver lock + BEGIN IMMEDIATE replace it).
4. (xmax = 0) AS <alias> → 1 AS <alias>, preserving the alias.
5. %s → ? but %% literal preserved and %(name)s ignored.
6. Mixed-case / whitespace tolerance — re patterns use IGNORECASE.
7. Idempotency: translating already-SQLite SQL is a no-op.
8. Multiple replacements per query handled.
9. Empty / None / whitespace-only inputs.
10. Driver singleton — get_driver() returns same instance.
"""
from __future__ import annotations


def test_greatest_translated_to_max():
    from db_driver import _pg_to_sqlite
    sql = "SELECT GREATEST(credits_remaining - 1, 0) FROM users"
    out = _pg_to_sqlite(sql)
    assert "GREATEST" not in out
    assert "MAX(" in out


def test_least_translated_to_min():
    from db_driver import _pg_to_sqlite
    sql = "UPDATE users SET cap = LEAST(cap, 100)"
    out = _pg_to_sqlite(sql)
    assert "LEAST" not in out
    assert "MIN(" in out


def test_greatest_least_case_insensitive():
    from db_driver import _pg_to_sqlite
    for verb in ("greatest", "Greatest", "GREATEST", "gReAtEsT"):
        out = _pg_to_sqlite(f"SELECT {verb}(a, b)")
        assert "MAX(" in out
        assert verb.lower() not in out.lower() or "max(" in out.lower()


def test_serial_primary_key_translated():
    from db_driver import _pg_to_sqlite
    out = _pg_to_sqlite("CREATE TABLE t (id SERIAL PRIMARY KEY)")
    assert "INTEGER PRIMARY KEY AUTOINCREMENT" in out
    assert "SERIAL" not in out


def test_serial_primary_key_extra_whitespace():
    """Multiple spaces / tabs between SERIAL and PRIMARY KEY tolerated."""
    from db_driver import _pg_to_sqlite
    out = _pg_to_sqlite("CREATE TABLE t (id SERIAL    PRIMARY  KEY)")
    assert "INTEGER PRIMARY KEY AUTOINCREMENT" in out
    assert "SERIAL" not in out


def test_for_update_stripped():
    from db_driver import _pg_to_sqlite
    out = _pg_to_sqlite("SELECT * FROM t WHERE id = 1 FOR UPDATE")
    assert "FOR UPDATE" not in out.upper()


def test_for_update_case_insensitive():
    from db_driver import _pg_to_sqlite
    for verb in ("for update", "For Update", "FOR  UPDATE"):
        out = _pg_to_sqlite(f"SELECT * FROM t {verb}")
        # All variants gone.
        assert "FOR UPDATE" not in out.upper().replace("  ", " ")


def test_xmax_replaced_preserves_alias():
    from db_driver import _pg_to_sqlite
    out = _pg_to_sqlite("INSERT ... RETURNING (xmax = 0) AS was_inserted")
    assert "xmax" not in out.lower()
    assert "1 AS was_inserted" in out


def test_xmax_replacement_with_extra_whitespace():
    from db_driver import _pg_to_sqlite
    out = _pg_to_sqlite("RETURNING (  xmax  =  0  ) AS inserted_flag")
    assert "1 AS inserted_flag" in out


def test_placeholder_pg_to_sqlite():
    from db_driver import _pg_to_sqlite
    out = _pg_to_sqlite("SELECT * FROM t WHERE a = %s AND b = %s")
    assert out.count("?") == 2
    assert "%s" not in out


def test_placeholder_preserves_double_percent():
    """%% is a literal % in PostgreSQL parameter formatting; must NOT
    be translated to ?. Otherwise ILIKE patterns like 'foo%%' break."""
    from db_driver import _pg_to_sqlite
    out = _pg_to_sqlite("SELECT * FROM t WHERE name LIKE 'foo%%bar'")
    # %% literal stays.
    assert "%%" in out


def test_placeholder_preserves_named():
    """%(name)s is psycopg2 named-parameter syntax; not the same as %s.
    Naive `%s → ?` substitution must skip these."""
    from db_driver import _pg_to_sqlite
    out = _pg_to_sqlite("SELECT * FROM t WHERE id = %(user_id)s")
    # Named placeholder kept (psycopg2 handles it; SQLite path doesn't
    # use %(name)s currently — but the regex must not corrupt it).
    assert "%(user_id)s" in out


def test_translation_idempotent_on_already_translated():
    """Running translation twice must not double-translate — e.g.
    second pass shouldn't turn ? into ?? or MAX( into MAX(MAX(."""
    from db_driver import _pg_to_sqlite
    sql = "SELECT GREATEST(a, 0), b FROM t WHERE id = %s FOR UPDATE"
    once = _pg_to_sqlite(sql)
    twice = _pg_to_sqlite(once)
    assert once == twice


def test_translation_handles_empty_string():
    from db_driver import _pg_to_sqlite
    assert _pg_to_sqlite("") == ""


def test_translation_handles_none():
    """Defensive: None must not raise — guards against a None being
    passed by a caller mid-build."""
    from db_driver import _pg_to_sqlite
    assert _pg_to_sqlite(None) is None  # type: ignore[arg-type]


def test_translation_chained_replacements():
    """A query with all 5 PG-isms must translate them all in one pass."""
    from db_driver import _pg_to_sqlite
    sql = (
        "INSERT INTO t (id, name) VALUES (%s, %s) "
        "ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name "
        "RETURNING (xmax = 0) AS was_inserted, "
        "GREATEST(credits, 0) AS c FOR UPDATE"
    )
    out = _pg_to_sqlite(sql)
    assert "%s" not in out
    assert "?" in out
    assert "xmax" not in out.lower()
    assert "1 AS was_inserted" in out
    assert "GREATEST" not in out
    assert "MAX(" in out
    assert "FOR UPDATE" not in out.upper()


def test_get_driver_singleton(local_env):
    """get_driver() must return the same driver instance across calls
    in the same process — db.py and db_driver-aware callers cache
    placeholder/cursor_factory references on first call."""
    from db_driver import get_driver
    d1 = get_driver()
    d2 = get_driver()
    assert d1 is d2


def test_sqlite_driver_translate_sql_uses_translation(local_env):
    """The SQLite driver's translate_sql method must apply
    _pg_to_sqlite — otherwise db.py's PG syntax crashes on SQLite."""
    from db_driver import get_driver
    d = get_driver()
    if d.name == "sqlite":
        out = d.translate_sql("SELECT %s, GREATEST(a, 0) FOR UPDATE")
        assert "?" in out
        assert "MAX(" in out
        assert "FOR UPDATE" not in out.upper()


def test_sqlite_placeholder_attribute(local_env):
    """SQLite driver exposes placeholder='?' (PG would be '%s'). db.py
    reads this when building dynamic IN clauses."""
    from db_driver import get_driver
    d = get_driver()
    if d.name == "sqlite":
        assert d.placeholder == "?"
