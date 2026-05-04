"""Regression tests for a621 (BRAIN-RESET-DNA-CASCADE):
wizard reset must cascade to the `agent_dna` table.

Failure mode (Per Huntova engineering brain persistence
audit, wave 2):

The user-facing `/api/wizard/reset` (BRAIN-80, a441) and the
admin `/api/ops/users/{id}/wizard/reset` (BRAIN-95, a464)
both wipe `s["wizard"] = {}` via `db.merge_settings`. Per
templates/jarvis.html line 3759 the user-facing reset
confirm copy reads:

    "Restart the Brain? This wipes all wizard answers,
     training, and DNA generation state on the server."

But before this fix, neither reset endpoint touched the
`agent_dna` table. The orphaned DNA row from the PRIOR
business persisted forever. Next hunt's `run_agent_scoped`
in app.py loaded that row via `db.get_agent_dna(...)` and
ran the new (totally different ICP) hunt with stale
`search_queries` + `scoring_rules`. The new wizard's DNA
regeneration only OVERWROTE on success — if the AI provider
rate-limited or returned malformed JSON, the stale DNA stuck
(a340 surfaced this as a "warn" log but never cleared the
cache).

Per durable-workflow guidance already cited in BRAIN-80:
reset must create a clean new run, not reuse leftover
derived outputs. Once you persist workflow status, reset
semantics must be equally durable + complete — across all
derived tables, not just the headline one.

Invariants:
- `db.delete_agent_dna(user_id)` exists, deletes the row,
  returns True when a row was wiped.
- `api_wizard_reset` calls `db.delete_agent_dna` AFTER the
  merge_settings wipe (order matters: stop bleeding before
  wiping derived state, in case the merge fails for some
  reason and the row should stay correlated).
- `admin_wizard_reset` calls `db.delete_agent_dna` (parity
  with BRAIN-95).
- Both reset endpoints stop any currently-running agent so
  it can't keep using the in-memory cached brain / DNA
  captured at hunt start (`run_agent_scoped` loads brain
  ONCE at start, never re-reads).
- The audit-log entry for admin reset must capture
  `dna_wiped` so operators have a paper trail when the
  cascade actually fired.
"""
from __future__ import annotations
import inspect
import re


def test_db_delete_agent_dna_exists_and_returns_bool():
    """Source-level: db.delete_agent_dna exists and is async."""
    import db
    assert hasattr(db, "delete_agent_dna"), (
        "BRAIN-RESET-DNA-CASCADE regression: db must export "
        "`delete_agent_dna(user_id)` so the wizard reset "
        "endpoints have an atomic cascade primitive instead "
        "of inlining DELETE SQL."
    )
    import inspect as _inspect
    assert _inspect.iscoroutinefunction(db.delete_agent_dna), (
        "BRAIN-RESET-DNA-CASCADE regression: delete_agent_dna "
        "must be async — all other db.* helpers are async + "
        "the reset endpoints await their callers."
    )


def test_db_delete_agent_dna_targets_correct_table_with_param():
    """Source-level: the DELETE statement must hit `agent_dna`
    + use a parameterised user_id (never f-string interpolation
    — DB safety rule #3 in CLAUDE.md)."""
    import db
    src = inspect.getsource(db.delete_agent_dna)
    assert "agent_dna" in src, (
        "BRAIN-RESET-DNA-CASCADE regression: delete_agent_dna "
        "must DELETE from the agent_dna table."
    )
    assert "DELETE FROM agent_dna WHERE user_id = %s" in src, (
        "BRAIN-RESET-DNA-CASCADE regression: must use the "
        "exact parameterised form `DELETE FROM agent_dna "
        "WHERE user_id = %s`. Never f-string interpolate "
        "user_id (CLAUDE.md rule 3)."
    )
    # Forbid the unsafe forms.
    assert "f\"DELETE" not in src and "f'DELETE" not in src, (
        "BRAIN-RESET-DNA-CASCADE regression: delete_agent_dna "
        "must not f-string-interpolate SQL. Use the "
        "parameterised %s form."
    )


def test_user_reset_calls_delete_agent_dna():
    """Source-level: /api/wizard/reset must cascade to DNA."""
    from server import api_wizard_reset
    src = inspect.getsource(api_wizard_reset)
    assert "delete_agent_dna" in src, (
        "BRAIN-RESET-DNA-CASCADE regression: /api/wizard/reset "
        "must call db.delete_agent_dna so a brain reset "
        "actually clears the DNA cache the next hunt would "
        "otherwise load. Pre-fix the orphaned DNA from the "
        "prior business silently survived the reset."
    )


def test_user_reset_cascade_runs_after_merge_settings():
    """Source-level: the cascade must fire AFTER the
    merge_settings wipe completes. If we delete the DNA first
    and the merge_settings call then fails, we'd be left
    with stale wizard data + no DNA (worse than pre-fix
    — the agent would refuse to run instead of running with
    stale context). Order matters."""
    from server import api_wizard_reset
    src = inspect.getsource(api_wizard_reset)
    # Strip docstring + comments so we only see live code order.
    code_only = re.sub(r'""".*?"""', "", src, flags=re.DOTALL)
    code_only = re.sub(r"#[^\n]*", "", code_only)
    merge_pos = code_only.find("merge_settings(")
    delete_pos = code_only.find("delete_agent_dna(")
    assert merge_pos > -1 and delete_pos > -1, (
        "BRAIN-RESET-DNA-CASCADE regression: both calls must "
        "appear in api_wizard_reset."
    )
    assert merge_pos < delete_pos, (
        "BRAIN-RESET-DNA-CASCADE regression: db.merge_settings "
        "must run BEFORE db.delete_agent_dna. Otherwise a "
        "merge_settings failure leaves a half-fixed state "
        "(no DNA + stale wizard) that's worse than the "
        "original bug."
    )


def test_user_reset_stops_running_agent():
    """Source-level: /api/wizard/reset must stop any
    currently-running agent. The agent loads brain + DNA
    ONCE at hunt start (run_agent_scoped in app.py), so a
    reset mid-hunt with no stop call leaves the running
    hunt happily targeting the OLD ICP until completion."""
    from server import api_wizard_reset
    src = inspect.getsource(api_wizard_reset)
    assert "agent_runner" in src, (
        "BRAIN-RESET-DNA-CASCADE regression: /api/wizard/reset "
        "must import agent_runner to stop any in-flight hunt. "
        "The agent's _brain and _cached_dna are captured at "
        "start and never re-read mid-hunt."
    )
    assert "stop_agent" in src, (
        "BRAIN-RESET-DNA-CASCADE regression: /api/wizard/reset "
        "must call agent_runner.stop_agent so a hunt can't "
        "keep using stale in-memory brain after the wipe."
    )
    assert "is_running" in src, (
        "BRAIN-RESET-DNA-CASCADE regression: /api/wizard/reset "
        "must guard the stop call with is_running so it's a "
        "no-op when no hunt is active (otherwise we'd log "
        "spurious 'agent stopped' lines on every reset)."
    )


def test_admin_reset_calls_delete_agent_dna():
    """Source-level: /api/ops/users/{id}/wizard/reset must
    cascade to DNA — parity with the user-facing reset
    (BRAIN-95 explicitly requires parity)."""
    from server import admin_wizard_reset
    src = inspect.getsource(admin_wizard_reset)
    assert "delete_agent_dna" in src, (
        "BRAIN-RESET-DNA-CASCADE regression: admin reset must "
        "cascade to DNA. Parity with the user-facing reset is "
        "an explicit BRAIN-95 invariant — see the audit comment "
        "in admin_wizard_reset's docstring."
    )


def test_admin_reset_stops_running_agent():
    """Source-level: admin reset must stop a running agent
    (parity with user reset)."""
    from server import admin_wizard_reset
    src = inspect.getsource(admin_wizard_reset)
    assert "stop_agent" in src, (
        "BRAIN-RESET-DNA-CASCADE regression: admin reset must "
        "stop a running agent (parity with user reset)."
    )


def test_admin_reset_audit_log_records_dna_wiped():
    """Source-level: the admin audit-log payload must record
    whether DNA was actually wiped, so operators have a clean
    paper trail. Otherwise the BRAIN-95 audit-log promise
    (that operator actions are accountable) silently regresses
    when the cascade fires."""
    from server import admin_wizard_reset
    src = inspect.getsource(admin_wizard_reset)
    assert "dna_wiped" in src, (
        "BRAIN-RESET-DNA-CASCADE regression: admin reset's "
        "log_admin_action payload must include dna_wiped so "
        "ops can audit the cascade. Parity with the existing "
        "had_brain / had_dossier fields."
    )


def test_user_reset_response_advertises_cascade_outcome():
    """Source-level: the JSON response from /api/wizard/reset
    must surface dna_wiped so the frontend (which already
    promises 'wipes all wizard answers, training, and DNA
    generation state' in its confirm dialog) can verify the
    server actually did what it promised. Tests in higher
    layers can then assert the cascade fires end-to-end."""
    from server import api_wizard_reset
    src = inspect.getsource(api_wizard_reset)
    # Collapse whitespace then check for the literal that the
    # response dict contains.
    flat = re.sub(r"\s+", "", src)
    assert "\"dna_wiped\":" in flat, (
        "BRAIN-RESET-DNA-CASCADE regression: /api/wizard/reset "
        "response must include dna_wiped so the frontend / "
        "tests / curl users can confirm the cascade fired."
    )


def test_delete_agent_dna_swallows_exceptions():
    """Source-level: delete_agent_dna must NEVER raise — a
    failure in DNA cleanup should not block the wizard reset
    itself. Worst case the orphan stays for one more hunt
    and the next reset will catch it."""
    import db
    src = inspect.getsource(db.delete_agent_dna)
    assert "except Exception" in src, (
        "BRAIN-RESET-DNA-CASCADE regression: delete_agent_dna "
        "must catch Exception so a DNA-table problem can't "
        "block the wizard reset (which already succeeded by "
        "the time we get here — order is merge_settings → "
        "delete_agent_dna)."
    )


def test_reset_endpoints_use_db_helper_not_inline_sql():
    """Source-level: reset endpoints must use the new
    db.delete_agent_dna helper, not inline DELETE SQL. This
    is a code-organisation invariant — every other table-level
    operation lives in db.py so the SQLite/Postgres dialect
    handling stays in one place. Inlining DELETE in server.py
    would split the schema knowledge across two files."""
    from server import api_wizard_reset, admin_wizard_reset
    for fn, label in [
        (api_wizard_reset, "user-facing"),
        (admin_wizard_reset, "admin"),
    ]:
        src = inspect.getsource(fn)
        # Strip docstring + comments — only the live code
        # matters. Comments may legitimately reference the
        # SQL pattern when explaining the historical bug.
        code_only = re.sub(r'""".*?"""', "", src, flags=re.DOTALL)
        code_only = re.sub(r"#[^\n]*", "", code_only)
        assert "DELETE FROM agent_dna" not in code_only, (
            f"BRAIN-RESET-DNA-CASCADE regression: {label} "
            f"reset endpoint must call db.delete_agent_dna, "
            f"not inline a DELETE FROM agent_dna statement. "
            f"Schema knowledge lives in db.py."
        )
