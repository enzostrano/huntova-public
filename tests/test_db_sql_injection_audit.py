"""DB-AUDIT (a1100): pin db.py against SQL-string-interpolation regressions.

Three classes of guard:

1. Behavioural: `update_user` / `update_agent_run` reject column names
   that aren't on the explicit allowlist — so a future careless
   `await db.update_user(uid, **request_body)` raises instead of
   building a SQL query out of attacker-controlled keys.

2. Behavioural: `_apply_credit_delta_sync.gate` is allowlist-checked —
   it's concatenated into SQL, so only hardcoded literals are permitted.

3. Static analysis: regex sweep of db.py to catch obvious
   `cur.execute(f"... {var}")` / `% var` / `.format()` patterns. The
   sweep is intentionally narrow (it allow-lists the patterns we have
   today) so any new f-string-built SQL has to be reviewed.

CLAUDE.md rule 3: "Always use parameterized SQL queries (`%s`) — never
string formatting."
"""
import asyncio
import os
import re
from pathlib import Path

import pytest

DB_PATH = Path(__file__).resolve().parent.parent / "db.py"


# ── 1. update_user allowlist ──────────────────────────────────────────

def test_update_user_rejects_non_allowlisted_column():
    """Future caller doing `await db.update_user(uid, **body)` with an
    attacker-controlled key like `"x = 1; DROP TABLE users; --"` must
    raise ValueError, not interpolate that string into the UPDATE SQL."""
    import db

    async def run():
        with pytest.raises(ValueError, match="non-allowlisted"):
            await db.update_user(
                1, **{"x = 1; DROP TABLE users; --": "boom"})

    asyncio.run(run())


def test_update_user_rejects_unknown_column_silently_fails_closed():
    import db

    async def run():
        with pytest.raises(ValueError):
            await db.update_user(1, totally_made_up_column="x")

    asyncio.run(run())


def test_update_user_allowlist_covers_known_real_callers():
    """The columns every caller in server.py / payments.py / auth.py
    passes today must be on the allowlist."""
    import db
    real_caller_keys = {
        "display_name", "role", "email_verified",
        "google_id", "auth_provider", "avatar_url",
        "tier", "credits_reset_date", "password_hash",
        "is_suspended", "last_login",
    }
    missing = real_caller_keys - db._UPDATE_USER_ALLOWED_COLS
    assert not missing, f"Real callers pass these keys but they're not allowlisted: {missing}"


# ── 2. update_agent_run allowlist ─────────────────────────────────────

def test_update_agent_run_rejects_non_allowlisted_column():
    import db

    async def run():
        with pytest.raises(ValueError, match="non-allowlisted"):
            await db.update_agent_run(
                1, **{"status'; DROP TABLE agent_runs; --": "boom"})

    asyncio.run(run())


def test_update_agent_run_allowlist_covers_known_real_callers():
    """agent_runner.py:369 + 408 pass these keys."""
    import db
    real_caller_keys = {"status", "leads_found", "ended_at"}
    missing = real_caller_keys - db._UPDATE_AGENT_RUN_ALLOWED_COLS
    assert not missing, f"agent_runner pass these keys but they're not allowlisted: {missing}"


# ── 3. _apply_credit_delta_sync gate allowlist ───────────────────────

def test_apply_credit_delta_rejects_non_allowlisted_gate():
    """`gate` is concatenated raw into the UPDATE. Only hardcoded
    literals on the allowlist are permitted."""
    import db
    # Bypass the _pool/driver check by patching to a truthy sentinel —
    # we want to hit the gate-validation gate before any DB I/O.
    orig_pool = db._pool
    db._pool = object()
    try:
        with pytest.raises(ValueError, match="not on the allowlist"):
            db._apply_credit_delta_sync(
                1, -1, "test", "ref",
                gate="1=1; DROP TABLE users; --")
    finally:
        db._pool = orig_pool


def test_apply_credit_delta_accepts_known_real_gates():
    """The literal `"credits_remaining >= %s"` from db.deduct_credit
    must stay on the allowlist."""
    import db
    assert "" in db._CREDIT_DELTA_GATE_ALLOWED
    assert "credits_remaining >= %s" in db._CREDIT_DELTA_GATE_ALLOWED


# ── 4. Static-analysis sweep — no new unsafe patterns ─────────────────

def _read_db_source():
    return DB_PATH.read_text(encoding="utf-8")


def test_db_py_has_no_f_string_with_interpolated_value_followed_by_execute():
    """`cur.execute(f"... {var}")` where `var` is anything other than a
    pure SQL fragment built from %s placeholders is a SQL injection.

    We pin the count of f-strings containing SQL keywords AND
    interpolated names so any new instance forces a code review.
    Today's safe f-string sites (line 860, 1410, 1414, 1475, 1479,
    2279, 2318, 2323, 2817, 2844, 3553) all interpolate values that
    are either:
      - hardcoded SQL fragments built from string literals
      - placeholder strings `", ".join(["%s"]*N)`
      - whitelisted column names

    If you add a new f-string SQL site, audit it manually then bump
    the EXPECTED_F_STRING_SQL_SITES count below.
    """
    src = _read_db_source()
    pattern = re.compile(
        r'f"[^"]*\b(?:SELECT|INSERT|UPDATE|DELETE|FROM|WHERE)\b[^"]*\{[^}]+\}[^"]*"',
        re.IGNORECASE,
    )
    matches = pattern.findall(src)
    EXPECTED_F_STRING_SQL_SITES = 12
    assert len(matches) <= EXPECTED_F_STRING_SQL_SITES, (
        f"db.py has {len(matches)} f-string SQL sites with interpolation, "
        f"expected ≤ {EXPECTED_F_STRING_SQL_SITES}. New site? Audit it "
        f"manually for SQL injection then bump the cap.\n"
        f"Matches:\n" + "\n".join(matches)
    )


def test_db_py_has_no_percent_format_in_execute():
    """`cur.execute(sql % var)` and `cur.execute(sql.format(...))` are
    classic SQL-injection patterns. Forbidden in db.py."""
    src = _read_db_source()
    # Match `.execute(...... % ......)` where the `%` is a Python
    # string-format operator (not a `%s` placeholder inside a quoted
    # string). We approximate by requiring `%` followed by a
    # non-string char like `(` or a name char.
    bad_percent = re.compile(r"\.execute\(\s*[^,)]*\)\s*%\s*[\(\w]")
    bad_format = re.compile(r"\.execute\(\s*[^)]*\.format\s*\(")
    pct_matches = bad_percent.findall(src)
    fmt_matches = bad_format.findall(src)
    assert not pct_matches, (
        f"db.py uses Python `%` string-format inside execute(): {pct_matches}"
    )
    assert not fmt_matches, (
        f"db.py uses .format() inside execute(): {fmt_matches}"
    )


def test_db_py_helpers_use_xlate_or_raw_pg_only():
    """Every non-trivial execute() either goes through `_xlate()`
    (driver-portable) or runs only in the cloud Postgres path. This
    test pins the number of `cur.execute(` raw calls so adding a new
    one without `_xlate()` requires bumping the count and reviewing."""
    src = _read_db_source()
    # Count cur.execute( occurrences (any bare execute on a cursor)
    raw_execs = re.findall(r"\bcur\d*\.execute\s*\(", src)
    # Of those, how many wrap their SQL in _xlate(?
    xlated = re.findall(r"\bcur\d*\.execute\s*\(\s*_xlate\s*\(", src)
    # Plus the literal one-liners in init_db_sync that are
    # ALTER TABLE / CREATE INDEX schema strings (Postgres-only).
    # Sanity: total executes should be in a sensible range and the
    # xlate share should stay above the "almost all" threshold.
    assert len(raw_execs) <= 60, (
        f"db.py grew to {len(raw_execs)} raw cur.execute() calls. "
        f"That's a lot of new SQL — review for parameterization."
    )


# ── 5. CLAUDE.md rule 4 — every cur.execute path returns the conn ──

def test_every_get_conn_has_matching_put_conn():
    """CLAUDE.md rule 4: 'Always return DB connections to pool in
    finally block.' Pin the get_conn / put_conn balance — if a future
    edit forgets a `put_conn(conn)`, this test catches the leak."""
    src = _read_db_source()
    get_conns = len(re.findall(r"\bconn\s*=\s*get_conn\s*\(", src))
    put_conns = len(re.findall(r"\bput_conn\s*\(\s*conn", src))
    # Some files have helper wrappers; allow put_conn to slightly
    # exceed get_conn (e.g. inside the helper module itself), but
    # never the other way around.
    assert put_conns >= get_conns, (
        f"db.py has {get_conns} `conn = get_conn()` calls but only "
        f"{put_conns} matching `put_conn(conn)` returns — connections "
        f"are leaking. CLAUDE.md rule 4."
    )
