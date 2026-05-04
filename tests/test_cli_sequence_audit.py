"""BRAIN-184: cli_sequence.py cadence + due-check helper audit.

Pure functions in `huntova sequence run`. Pinned invariants:

1. `_user_cadence` returns built-in cadence when settings is None.
2. `_user_cadence` returns empty list when `sequence_enabled` is "off".
3. `_user_cadence` honours `follow_up_{1,2,3}_days` overrides; values
   stored as deltas (step 2 delta = days_1; step 3 delta = days_2 -
   days_1; step 4 delta = days_3 - days_1 - days_2).
4. `_user_cadence` clamps deltas to ≥0 (no negative days).
5. `_user_cadence` defaults missing values to 4/9 days.
6. `_first_name` extracts first whitespace-split token; falls back to
   "there" on empty / None.
7. `_recap` pulls first paragraph of opener; clamps to 220 chars with
   ellipsis.
8. `_booking_line` returns empty when URL absent.
9. `_due` returns False when `_seq_last_at` empty / unparseable.
10. `_due` returns True only when delta_days have elapsed since last.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def test_user_cadence_none_settings_falls_back():
    from cli_sequence import _user_cadence, _CADENCE
    out = _user_cadence(None)
    assert out == list(_CADENCE)


def test_user_cadence_disabled_returns_empty():
    from cli_sequence import _user_cadence
    out = _user_cadence({"sequence_enabled": "off"})
    assert out == []
    # case-insensitive.
    out = _user_cadence({"sequence_enabled": "OFF"})
    assert out == []
    # Whitespace tolerant.
    out = _user_cadence({"sequence_enabled": "  off  "})
    assert out == []


def test_user_cadence_default_days():
    """Without explicit overrides, defaults to 4/9 day cadence."""
    from cli_sequence import _user_cadence
    out = _user_cadence({})  # no overrides
    assert out[0] == (2, 4, "bump")
    # step 3 delta = 9 - 4 = 5.
    assert out[1] == (3, 5, "final")


def test_user_cadence_explicit_overrides():
    from cli_sequence import _user_cadence
    out = _user_cadence({
        "follow_up_1_days": 3,
        "follow_up_2_days": 10,
    })
    # step 2 fires after 3 days from initial.
    assert out[0] == (2, 3, "bump")
    # step 3 fires 10 - 3 = 7 days after step 2.
    assert out[1] == (3, 7, "final")


def test_user_cadence_third_step_optional():
    """`follow_up_3_days` is optional; absence means only 3-step
    sequence (steps 2, 3)."""
    from cli_sequence import _user_cadence
    out = _user_cadence({"follow_up_1_days": 4, "follow_up_2_days": 9})
    assert len(out) == 2


def test_user_cadence_third_step_when_set():
    from cli_sequence import _user_cadence
    out = _user_cadence({
        "follow_up_1_days": 4,
        "follow_up_2_days": 9,
        "follow_up_3_days": 16,
    })
    assert len(out) == 3
    assert out[2][0] == 4  # step number
    # delta = 16 - 4 - 5 = 7.
    assert out[2][1] == 7


def test_user_cadence_clamps_negative_to_zero():
    """If user sets follow_up_2_days < follow_up_1_days, delta would
    be negative — must clamp to 0."""
    from cli_sequence import _user_cadence
    out = _user_cadence({"follow_up_1_days": 10, "follow_up_2_days": 5})
    # delta_2 = max(0, 5 - 10) = 0
    assert out[1][1] == 0


def test_user_cadence_handles_non_numeric_garbage():
    """Non-numeric `follow_up_N_days` falls back to default tuple."""
    from cli_sequence import _user_cadence, _CADENCE
    out = _user_cadence({"follow_up_1_days": "not-a-number"})
    # Step 2 falls back to the built-in default.
    assert out[0] == _CADENCE[0]


def test_first_name_extracts_first_token():
    from cli_sequence import _first_name
    assert _first_name("Alice Smith") == "Alice"
    assert _first_name("Alice") == "Alice"
    assert _first_name("Alice  Bob  Smith") == "Alice"  # collapses spaces


def test_first_name_empty_or_none():
    from cli_sequence import _first_name
    assert _first_name(None) == "there"
    assert _first_name("") == "there"
    assert _first_name("   ") == "there"


def test_first_name_strips_whitespace():
    from cli_sequence import _first_name
    assert _first_name("  Alice  ") == "Alice"


def test_recap_first_paragraph():
    from cli_sequence import _recap
    body = "First paragraph.\n\nSecond paragraph here."
    assert _recap(body) == "First paragraph."


def test_recap_clamps_to_220():
    from cli_sequence import _recap
    long_para = "x" * 500
    out = _recap(long_para)
    assert len(out) <= 221  # 220 + ellipsis
    assert out.endswith("…")


def test_recap_short_no_ellipsis():
    from cli_sequence import _recap
    out = _recap("short note")
    assert out == "short note"
    assert "…" not in out


def test_recap_handles_empty():
    from cli_sequence import _recap
    assert _recap("") == ""
    assert _recap(None) == ""


def test_booking_line_empty_url():
    from cli_sequence import _booking_line
    assert _booking_line("") == ""
    assert _booking_line(None) == ""


def test_booking_line_with_url():
    from cli_sequence import _booking_line
    out = _booking_line("https://cal.com/me")
    assert "cal.com" in out
    assert out.endswith("\n\n")


def test_due_no_last_at_returns_false():
    from cli_sequence import _due
    assert _due(_seq_step=1, _seq_last_at=None) is False
    assert _due(_seq_step=1, _seq_last_at="") is False


def test_due_unparseable_returns_false():
    from cli_sequence import _due
    assert _due(_seq_step=1, _seq_last_at="not-a-date") is False


def test_due_returns_false_when_too_recent():
    """Last at NOW → not due (delta not elapsed)."""
    from cli_sequence import _due
    now = datetime.now(timezone.utc).isoformat()
    assert _due(_seq_step=1, _seq_last_at=now) is False


def test_due_returns_true_when_delta_elapsed():
    from cli_sequence import _due
    # Last at 10 days ago, default cadence step 2 = 4 days, due.
    long_ago = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    assert _due(_seq_step=1, _seq_last_at=long_ago) is True


def test_due_handles_z_suffix():
    from cli_sequence import _due
    long_ago = (datetime.now(timezone.utc) - timedelta(days=10))
    # Add Z suffix the way the agent would.
    s = long_ago.isoformat().replace("+00:00", "Z")
    assert _due(_seq_step=1, _seq_last_at=s) is True


def test_due_naive_treated_as_utc():
    """Last at without timezone info is treated as UTC."""
    from cli_sequence import _due
    long_ago = (datetime.now(timezone.utc) - timedelta(days=10))
    naive = long_ago.replace(tzinfo=None).isoformat()
    assert _due(_seq_step=1, _seq_last_at=naive) is True


def test_due_step_beyond_cadence_returns_false():
    """No more steps in the cadence → not due (no further follow-up)."""
    from cli_sequence import _due
    long_ago = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
    # Default cadence has 2 steps (step 2 + step 3); step 5 is beyond.
    assert _due(_seq_step=5, _seq_last_at=long_ago) is False
