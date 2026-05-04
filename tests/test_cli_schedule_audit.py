"""BRAIN-172: cli_schedule.py time-parser + chain-builder audit.

`_parse_at` accepts user-provided "HH:MM" times with defensive
clamping (HH max 23, MM max 59, garbage input → 9:00 default).
`_build_chain` builds the daily command chain that launchd / systemd /
cron run. Both are pure functions (testable in isolation).

Pinned invariants:

1. `_parse_at` valid inputs round-trip.
2. `_parse_at` clamps out-of-range hours/minutes.
3. `_parse_at` defaults to 9:00 on garbage / empty input.
4. `_build_chain` always begins with the log-dir mkdir step.
5. `_build_chain` chains the canonical 4-step sequence.
6. `_build_chain` honours `with_update=False` to skip the update step.
7. `_build_chain` uses `$HOME` (not absolute path) so the chain
   survives launchd's HOME context differences.
8. `_emit_launchd` produces well-formed XML with the expected keys.
9. `_emit_cron` produces a single line with HH MM in the right slots.
"""
from __future__ import annotations


def test_parse_at_valid_hhmm():
    from cli_schedule import _parse_at
    assert _parse_at("09:00") == (9, 0)
    assert _parse_at("00:00") == (0, 0)
    assert _parse_at("23:59") == (23, 59)
    assert _parse_at("12:34") == (12, 34)


def test_parse_at_clamps_hour_overflow():
    from cli_schedule import _parse_at
    h, _ = _parse_at("25:00")
    assert h == 23
    h, _ = _parse_at("99:00")
    assert h == 23


def test_parse_at_clamps_minute_overflow():
    from cli_schedule import _parse_at
    _, m = _parse_at("09:61")
    assert m == 59
    _, m = _parse_at("09:99")
    assert m == 59


def test_parse_at_clamps_negatives():
    from cli_schedule import _parse_at
    h, m = _parse_at("-5:-10")
    assert h == 0
    assert m == 0


def test_parse_at_garbage_defaults():
    from cli_schedule import _parse_at
    assert _parse_at("not-a-time") == (9, 0)
    assert _parse_at("") == (9, 0)
    assert _parse_at(None) == (9, 0)  # type: ignore[arg-type]
    assert _parse_at(":::") == (9, 0)
    assert _parse_at("9") == (9, 0)  # missing colon → fallback


def test_parse_at_strips_whitespace():
    from cli_schedule import _parse_at
    assert _parse_at("  10:30  ") == (10, 30)


def test_parse_at_zero_padded_minutes():
    from cli_schedule import _parse_at
    assert _parse_at("09:05") == (9, 5)


def test_parse_at_garbage_hour():
    from cli_schedule import _parse_at
    # Non-numeric hour → fallback.
    assert _parse_at("abc:30") == (9, 0)
    assert _parse_at("0x1A:30") == (9, 0)


def test_build_chain_starts_with_mkdir():
    from cli_schedule import _build_chain
    chain = _build_chain("/usr/local/bin/huntova", 50)
    assert chain.startswith('mkdir -p "$HOME/.local/share/huntova/logs"')


def test_build_chain_uses_dollar_home():
    """Chain must use $HOME (so launchd HOME context differences
    don't break the path), not an interpolated absolute path."""
    from cli_schedule import _build_chain
    chain = _build_chain("/usr/local/bin/huntova", 50)
    assert "$HOME" in chain


def test_build_chain_contains_canonical_steps():
    from cli_schedule import _build_chain
    chain = _build_chain("/usr/local/bin/huntova", 25)
    # Sequence run, inbox check, pulse --since 1d.
    assert "sequence run --max 25" in chain
    assert "inbox check" in chain
    assert "pulse --since 1d" in chain


def test_build_chain_includes_update_check_by_default():
    from cli_schedule import _build_chain
    chain = _build_chain("/usr/local/bin/huntova", 50, with_update=True)
    assert "update --check" in chain


def test_build_chain_omits_update_when_disabled():
    from cli_schedule import _build_chain
    chain = _build_chain("/usr/local/bin/huntova", 50, with_update=False)
    assert "update --check" not in chain


def test_build_chain_passes_through_max_send():
    from cli_schedule import _build_chain
    chain = _build_chain("/usr/local/bin/huntova", 100)
    assert "--max 100" in chain


def test_emit_launchd_has_expected_keys():
    from cli_schedule import _emit_launchd
    plist = _emit_launchd(at="09:30", max_send=50, label="com.test.huntova")
    assert "<key>Label</key>" in plist
    assert "<string>com.test.huntova</string>" in plist
    assert "<key>ProgramArguments</key>" in plist
    assert "<key>StartCalendarInterval</key>" in plist
    assert "<key>Hour</key>" in plist
    assert "<integer>9</integer>" in plist
    assert "<key>Minute</key>" in plist
    assert "<integer>30</integer>" in plist


def test_emit_launchd_pads_minute():
    """Minutes should always be 2-digit zero-padded inside <integer>."""
    from cli_schedule import _emit_launchd
    plist = _emit_launchd(at="09:05", max_send=50, label="com.x")
    # Should NOT be <integer>5</integer>.
    assert "<integer>05</integer>" in plist


def test_emit_cron_format():
    """`_emit_cron` returns a 2-line string: a comment line + the
    cron entry. Find the entry line and verify minute / hour slots."""
    from cli_schedule import _emit_cron
    out = _emit_cron(at="09:30", max_send=50)
    # Locate the non-comment, non-blank line.
    entry = next(line for line in out.splitlines()
                 if line.strip() and not line.lstrip().startswith("#"))
    parts = entry.split()
    # Standard cron: M H * * * <command>
    assert parts[0] == "30"
    assert parts[1] == "9"
    assert parts[2] == "*"
    assert parts[3] == "*"
    assert parts[4] == "*"


def test_emit_cron_default_time():
    """Default time (no `at` arg or empty) → 9:00."""
    from cli_schedule import _emit_cron
    out = _emit_cron(at="", max_send=50)
    entry = next(line for line in out.splitlines()
                 if line.strip() and not line.lstrip().startswith("#"))
    parts = entry.split()
    assert parts[0] == "0"
    assert parts[1] == "9"
