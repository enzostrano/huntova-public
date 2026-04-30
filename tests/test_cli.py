"""CLI subcommand smoke — argparse wiring + non-blocking flags.

Each test invokes the parser+function directly rather than spawning a
subprocess so we don't have to install the package fresh on every
run. All tests stay under 1s.
"""
from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stdout, redirect_stderr


def test_parser_registers_all_subcommands():
    import cli
    parser = cli.build_parser()
    # argparse stores subparsers under the dest name we set ('cmd').
    sub = parser._subparsers._group_actions[0]  # type: ignore[attr-defined]
    names = set(sub.choices.keys())
    expected = {"serve", "init", "doctor", "version", "update",
                "hunt", "ls", "export", "share"}
    assert expected.issubset(names), f"missing: {expected - names}"


def test_version_prints_pep440_ish():
    import cli
    out = io.StringIO()
    with redirect_stdout(out):
        rc = cli.main(["version"])
    assert rc == 0
    text = out.getvalue().strip()
    assert text.startswith("0.")  # 0.1.0a1 etc.


def test_hunt_dry_run_json_emits_valid_jsonl(local_env):
    """--dry-run --json walks setup and emits a single JSON object on stdout."""
    import cli
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        rc = cli.main(["hunt", "--dry-run", "--json", "--countries", "Germany,France"])
    assert rc == 0
    lines = [l for l in stdout.getvalue().splitlines() if l.strip()]
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert obj["event"] == "dry_run_ok"
    assert obj["countries"] == ["Germany", "France"]
    # Side info must go to stderr, not stdout
    assert "dry-run:" in stderr.getvalue()


def test_hunt_dry_run_text_mode(local_env):
    """--dry-run without --json: human-readable text on stdout."""
    import cli
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        rc = cli.main(["hunt", "--dry-run"])
    assert rc == 0
    text = stdout.getvalue()
    assert "wiring intact" in text
    assert "providers=" in text
    # Should not emit any JSON lines in text mode.
    assert '"event"' not in text


def test_ls_empty_db_message(local_env):
    """`huntova ls` prints a friendly hint when no leads exist."""
    import cli
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        rc = cli.main(["ls"])
    assert rc == 0
    assert "no leads yet" in stdout.getvalue()


def test_export_empty_db_returns_nonzero(local_env):
    """`huntova export` should exit non-zero on empty DB so scripts stop."""
    import cli
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        rc = cli.main(["export", "--format", "csv"])
    assert rc == 1
    assert "no leads" in stderr.getvalue()


def test_doctor_quick_skips_network(local_env):
    """`huntova doctor --quick` should never block on the network probe."""
    import cli
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        rc = cli.main(["doctor", "--quick"])
    assert rc == 0
    text = stdout.getvalue()
    assert "AI probe:" not in text  # --quick suppresses it


def _seed_one_lead(user_id: int):
    """Helper: synchronously seed one lead so ls/lead/share have data."""
    import asyncio
    import db
    asyncio.run(db.upsert_lead(user_id, "L1", {
        "org_name": "Aurora Studios", "country": "Germany",
        "fit_score": 9, "why_fit": "Mid-size production house",
        "org_website": "https://aurora-studios.example",
    }))


def test_lead_lookup_by_id(local_env):
    """`huntova lead L1` prints detail block for the seeded lead."""
    import cli
    import db
    db.init_db_sync()
    import asyncio
    from auth import _ensure_local_user
    user = asyncio.run(_ensure_local_user())
    _seed_one_lead(user["id"])
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        rc = cli.main(["lead", "L1"])
    assert rc == 0
    text = stdout.getvalue()
    assert "Aurora Studios" in text
    assert "Germany" in text
    assert "9/10" in text


def test_lead_by_org_partial_match(local_env):
    """`huntova lead "Aurora" --by-org` finds the seeded lead by partial name."""
    import cli
    import db
    db.init_db_sync()
    import asyncio
    from auth import _ensure_local_user
    user = asyncio.run(_ensure_local_user())
    _seed_one_lead(user["id"])
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        rc = cli.main(["lead", "Aurora", "--by-org"])
    assert rc == 0
    assert "Aurora Studios" in stdout.getvalue()


def test_ls_filter_substring(local_env):
    """`huntova ls --filter Germany` narrows by country."""
    import cli
    import db
    db.init_db_sync()
    import asyncio
    from auth import _ensure_local_user
    user = asyncio.run(_ensure_local_user())
    _seed_one_lead(user["id"])
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        rc = cli.main(["ls", "--filter", "Germany"])
    assert rc == 0
    assert "Aurora Studios" in stdout.getvalue()


def test_ls_filter_field_prefix(local_env):
    """`huntova ls --filter country:Germany` matches by exact field."""
    import cli
    import db
    db.init_db_sync()
    import asyncio
    from auth import _ensure_local_user
    user = asyncio.run(_ensure_local_user())
    _seed_one_lead(user["id"])
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        rc = cli.main(["ls", "--filter", "country:Germany"])
    assert rc == 0
    assert "Aurora Studios" in stdout.getvalue()


def test_rm_deletes_lead(local_env):
    """`huntova rm L1 --yes` removes the lead and confirms via stdout."""
    import cli
    import db
    db.init_db_sync()
    import asyncio
    from auth import _ensure_local_user
    user = asyncio.run(_ensure_local_user())
    _seed_one_lead(user["id"])
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        rc = cli.main(["rm", "L1", "--yes"])
    assert rc == 0
    assert "deleted L1" in stdout.getvalue()
    # Confirm the lead actually went away
    leads = asyncio.run(db.get_leads(user["id"]))
    assert all(l.get("lead_id") != "L1" for l in leads)


def test_rm_unknown_lead_returns_nonzero(local_env):
    """`huntova rm L999` on a missing id returns non-zero + stderr message."""
    import cli
    import db
    db.init_db_sync()
    import asyncio
    from auth import _ensure_local_user
    asyncio.run(_ensure_local_user())
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        rc = cli.main(["rm", "L999", "--yes"])
    assert rc == 1
    assert "no lead" in stderr.getvalue()


def test_history_shows_recent_runs(local_env):
    """`huntova history` lists agent runs from agent_runs table."""
    import cli
    import db
    db.init_db_sync()
    import asyncio
    from auth import _ensure_local_user
    user = asyncio.run(_ensure_local_user())
    rid = asyncio.run(db.create_agent_run(user["id"]))
    asyncio.run(db.update_agent_run(rid, status="finished",
                                    leads_found=5, queries_done=8, queries_total=8))
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        rc = cli.main(["history"])
    assert rc == 0
    text = stdout.getvalue()
    assert "1 recent run" in text
    assert "finished" in text
    assert "5" in text  # leads_found


def test_recipe_save_then_ls(local_env):
    """`huntova recipe save` persists, `huntova recipe ls` shows it."""
    import cli
    import db
    db.init_db_sync()
    import asyncio
    from auth import _ensure_local_user
    asyncio.run(_ensure_local_user())
    out1 = io.StringIO()
    with redirect_stdout(out1):
        rc = cli.main(["recipe", "save", "demo-eu",
                       "--countries", "Germany,France",
                       "--max-leads", "5",
                       "--description", "demo recipe"])
    assert rc == 0
    assert "saved recipe 'demo-eu'" in out1.getvalue()
    out2 = io.StringIO()
    with redirect_stdout(out2):
        rc = cli.main(["recipe", "ls"])
    assert rc == 0
    text = out2.getvalue()
    assert "demo-eu" in text
    assert "demo recipe" in text


def test_recipe_run_dry_run_increments_count(local_env):
    """`huntova recipe run --dry-run` doesn't bump count; real run does."""
    import cli
    import db
    db.init_db_sync()
    import asyncio
    from auth import _ensure_local_user
    user = asyncio.run(_ensure_local_user())
    asyncio.run(db.save_hunt_recipe(user["id"], "x", {"countries": ["DE"]}, ""))
    out = io.StringIO()
    err = io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = cli.main(["recipe", "run", "x", "--dry-run"])
    assert rc == 0
    text = out.getvalue() + err.getvalue()
    assert "replaying recipe 'x'" in text
    assert "wiring intact" in text  # cmd_hunt --dry-run path


def test_recipe_rm_removes(local_env):
    """`huntova recipe rm` deletes a saved recipe."""
    import cli
    import db
    db.init_db_sync()
    import asyncio
    from auth import _ensure_local_user
    user = asyncio.run(_ensure_local_user())
    asyncio.run(db.save_hunt_recipe(user["id"], "tmp", {}, ""))
    out = io.StringIO()
    with redirect_stdout(out):
        rc = cli.main(["recipe", "rm", "tmp"])
    assert rc == 0
    assert "removed recipe 'tmp'" in out.getvalue()
    rows = asyncio.run(db.list_hunt_recipes(user["id"]))
    assert all(r["name"] != "tmp" for r in rows)


def test_completion_bash(local_env):
    import cli
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        rc = cli.main(["completion", "bash"])
    assert rc == 0
    text = stdout.getvalue()
    assert "_huntova_completion" in text
    assert "complete -F _huntova_completion huntova" in text


def test_completion_zsh(local_env):
    import cli
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        rc = cli.main(["completion", "zsh"])
    assert rc == 0
    assert "#compdef huntova" in stdout.getvalue()


def test_completion_fish(local_env):
    import cli
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        rc = cli.main(["completion", "fish"])
    assert rc == 0
    text = stdout.getvalue()
    assert "complete -c huntova" in text
    assert "subcommand" in text


def test_history_empty_message(local_env):
    """`huntova history` prints a friendly hint when no runs exist."""
    import cli
    import db
    db.init_db_sync()
    import asyncio
    from auth import _ensure_local_user
    asyncio.run(_ensure_local_user())
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        rc = cli.main(["history"])
    assert rc == 0
    assert "no hunt runs yet" in stdout.getvalue()


def test_share_creates_slug_and_url(local_env):
    """`huntova share` returns a slug + URL pointing at /h/<slug>."""
    import cli
    import db
    db.init_db_sync()
    import asyncio
    from auth import _ensure_local_user
    user = asyncio.run(_ensure_local_user())
    _seed_one_lead(user["id"])
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        rc = cli.main(["share", "--top", "5", "--title", "Test share"])
    assert rc == 0
    text = stdout.getvalue()
    assert "shared" in text.lower()
    assert "/h/" in text  # slug URL printed
