"""BRAIN-CLI-1 / BRAIN-CLI-2 regression — argparse UX hardening (a800).

Three behaviours we now guarantee:

  1. `huntova <typo>` suggests the closest real subcommand via difflib
     instead of dumping the giant `(choose from: …)` blob.
  2. `huntova <known-cmd> --bogus-flag` shows that subcommand's narrow
     usage line — not the top-level usage with all 40+ subcommands.
  3. The top-level `if __name__ == "__main__"` wrapper turns
     `KeyboardInterrupt` into exit code 130 so Ctrl+C never spills a
     Python traceback. Tested at the `main()` boundary so we don't
     need to spawn a subprocess + send SIGINT.

Each test exercises the parser/error path directly — no subprocess —
so the suite stays under 1s.
"""
from __future__ import annotations

import io
import sys
from contextlib import redirect_stderr, redirect_stdout

import pytest


# ────────────────────────── 1. typo suggestion ──────────────────────────

def test_typo_subcommand_suggests_closest_match():
    import cli
    err = io.StringIO()
    with redirect_stderr(err):
        with pytest.raises(SystemExit) as exc:
            cli.main(["hutn"])
    assert exc.value.code == 2
    text = err.getvalue()
    assert "unknown command: 'hutn'" in text
    assert "did you mean: hunt?" in text
    assert "huntova --help" in text
    # Crucially: the giant choices dump is gone.
    assert "choose from" not in text
    assert "test-integrations" not in text  # one of the 40+


def test_typo_subcommand_one_letter_off_still_suggests():
    """`huntova hunr` (one letter off `hunt`) should still suggest hunt."""
    import cli
    err = io.StringIO()
    with redirect_stderr(err):
        with pytest.raises(SystemExit) as exc:
            cli.main(["hunr"])
    assert exc.value.code == 2
    assert "did you mean: hunt?" in err.getvalue()


def test_random_garbage_subcommand_no_false_suggestion():
    """If nothing's close (e.g. 'zzzzzz'), don't fabricate a suggestion."""
    import cli
    err = io.StringIO()
    with redirect_stderr(err):
        with pytest.raises(SystemExit) as exc:
            cli.main(["zzzzzz"])
    assert exc.value.code == 2
    text = err.getvalue()
    assert "unknown command: 'zzzzzz'" in text
    assert "did you mean" not in text  # don't hallucinate
    assert "huntova --help" in text


# ──────────────── 2. unknown flag → narrow subparser usage ────────────────

def test_unknown_flag_uses_subcommand_usage_not_root():
    """`huntova hunt --bogus-flag` shows hunt's usage, not the 40-cmd blob."""
    import cli
    err = io.StringIO()
    with redirect_stderr(err):
        with pytest.raises(SystemExit) as exc:
            cli.main(["hunt", "--bogus-flag"])
    assert exc.value.code == 2
    text = err.getvalue()
    # Must show the subcommand-specific usage line.
    assert "usage: huntova hunt" in text
    assert "huntova hunt: error: unrecognized arguments: --bogus-flag" in text
    assert "run `huntova hunt --help`" in text
    # Must NOT show the top-level 40+ choice list.
    assert "test-integrations" not in text
    assert "{serve,tail,run" not in text


def test_unknown_flag_on_ls_uses_ls_usage():
    """Same behaviour for a different subcommand."""
    import cli
    err = io.StringIO()
    with redirect_stderr(err):
        with pytest.raises(SystemExit) as exc:
            cli.main(["ls", "--made-up"])
    assert exc.value.code == 2
    text = err.getvalue()
    assert "usage: huntova ls" in text
    assert "huntova ls: error: unrecognized arguments: --made-up" in text


# ────────────────────────── 3. version flag round-trip ──────────────────────

def test_top_level_version_flag_prints_version():
    import cli
    out = io.StringIO()
    with redirect_stdout(out):
        with pytest.raises(SystemExit) as exc:
            cli.main(["--version"])
    assert exc.value.code == 0
    assert out.getvalue().strip() == f"huntova {cli.VERSION}"


def test_version_subcommand_prints_version():
    import cli
    out = io.StringIO()
    with redirect_stdout(out):
        rc = cli.main(["version"])
    assert rc == 0
    assert out.getvalue().strip() == cli.VERSION


# ─────────────────────────── 4. parser internals ───────────────────────────

def test_parser_exposes_choice_metadata_for_error_handler():
    """`_HuntovaArgumentParser.error()` relies on these attrs being set."""
    import cli
    p = cli.build_parser()
    assert hasattr(p, "_huntova_choice_names")
    assert "hunt" in p._huntova_choice_names  # type: ignore[attr-defined]
    assert "ls" in p._huntova_choice_names    # type: ignore[attr-defined]
    assert hasattr(p, "_huntova_subparsers")
    assert "hunt" in p._huntova_subparsers    # type: ignore[attr-defined]


def test_help_still_renders_grouped_categories():
    """Sanity: the existing grouped-help layout survived the refactor."""
    import cli
    p = cli.build_parser()
    text = p.format_help()
    assert "Getting started:" in text
    assert "Daily use:" in text
    assert "Outreach:" in text
    # And subcommands appear under their categories.
    assert "hunt" in text
    assert "onboard" in text


# ────────────────────────── 5. KeyboardInterrupt ──────────────────────────

def test_main_returns_130_when_parser_raises_keyboard_interrupt(monkeypatch):
    """Ctrl+C during parse_args (e.g. interactive prompt) → exit 130."""
    import cli

    def _boom(*_a, **_kw):
        raise KeyboardInterrupt
    monkeypatch.setattr(cli._HuntovaArgumentParser, "parse_args", _boom)
    err = io.StringIO()
    with redirect_stderr(err):
        rc = cli.main(["hunt", "--dry-run"])
    assert rc == 130
