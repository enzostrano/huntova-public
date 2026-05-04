"""BRAIN-180: cli_logs.py log-level + since-parser invariant audit.

Pure helpers used by `huntova logs tail` / `huntova logs hunt <id>`.
Pinned invariants:

1. `_level_of` returns "error" when status is error/crashed/failed.
2. `_level_of` returns "error" when text matches error regex.
3. `_level_of` returns "warn" when text matches warn regex.
4. `_level_of` returns "info" when neither.
5. `_level_of` defensive on None / empty text + status.
6. `_parse_since` accepts s/m/h/d units case-insensitively.
7. `_parse_since` returns None for garbage / empty.
8. `_parse_since` returns datetime in UTC.
9. `_ts_short` truncates to 19 chars and replaces T with space.
10. `_ts_short` defensive on None / empty.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta


def test_level_of_error_status():
    from cli_logs import _level_of
    assert _level_of("normal text", "error") == "error"
    assert _level_of("normal text", "ERROR") == "error"
    assert _level_of("normal text", "crashed") == "error"
    assert _level_of("normal text", "failed") == "error"


def test_level_of_error_keyword_in_text():
    from cli_logs import _level_of
    assert _level_of("Exception: something broke") == "error"
    assert _level_of("ERROR: connection lost") == "error"


def test_level_of_warn_keyword_in_text():
    from cli_logs import _level_of
    assert _level_of("WARNING: deprecated") == "warn"


def test_level_of_info_default():
    from cli_logs import _level_of
    assert _level_of("hunt completed: 5 leads found") == "info"
    assert _level_of("queries 12/30") == "info"


def test_level_of_handles_none():
    from cli_logs import _level_of
    assert _level_of(None) == "info"  # type: ignore[arg-type]
    assert _level_of("") == "info"


def test_level_of_handles_none_status():
    from cli_logs import _level_of
    assert _level_of("ok", None) == "info"  # type: ignore[arg-type]


def test_parse_since_seconds():
    from cli_logs import _parse_since
    out = _parse_since("90s")
    assert out is not None
    diff = (datetime.now(timezone.utc) - out).total_seconds()
    assert 89 <= diff <= 91


def test_parse_since_minutes():
    from cli_logs import _parse_since
    out = _parse_since("30m")
    assert out is not None
    diff = (datetime.now(timezone.utc) - out).total_seconds() / 60
    assert 29 <= diff <= 31


def test_parse_since_hours():
    from cli_logs import _parse_since
    out = _parse_since("1h")
    assert out is not None
    diff = (datetime.now(timezone.utc) - out).total_seconds() / 3600
    assert 0.99 <= diff <= 1.01


def test_parse_since_days():
    from cli_logs import _parse_since
    out = _parse_since("2d")
    assert out is not None
    diff = (datetime.now(timezone.utc) - out).total_seconds() / 86400
    assert 1.99 <= diff <= 2.01


def test_parse_since_case_insensitive():
    from cli_logs import _parse_since
    a = _parse_since("1H")
    b = _parse_since("1h")
    assert a is not None and b is not None
    # Within 1 second of each other.
    assert abs((a - b).total_seconds()) < 1


def test_parse_since_handles_whitespace():
    from cli_logs import _parse_since
    assert _parse_since("  1h  ") is not None
    assert _parse_since(" 30 m ") is not None


def test_parse_since_returns_none_on_garbage():
    from cli_logs import _parse_since
    assert _parse_since("") is None
    assert _parse_since(None) is None  # type: ignore[arg-type]
    assert _parse_since("not-a-duration") is None
    assert _parse_since("1y") is None  # year unit not supported
    assert _parse_since("abc") is None


def test_parse_since_returns_utc():
    from cli_logs import _parse_since
    out = _parse_since("1h")
    assert out is not None
    assert out.tzinfo is not None
    assert out.utcoffset() == timedelta(0)


def test_ts_short_truncates_to_19():
    from cli_logs import _ts_short
    full = "2026-05-04T09:30:00.123456+00:00"
    out = _ts_short(full)
    assert len(out) == 19
    assert out == "2026-05-04 09:30:00"


def test_ts_short_replaces_t_with_space():
    from cli_logs import _ts_short
    out = _ts_short("2026-05-04T09:30:00")
    assert "T" not in out
    assert " " in out


def test_ts_short_handles_none():
    from cli_logs import _ts_short
    assert _ts_short(None) == ""  # type: ignore[arg-type]
    assert _ts_short("") == ""


def test_ts_short_short_input():
    """Input shorter than 19 chars stays as-is (no padding, no crash)."""
    from cli_logs import _ts_short
    assert _ts_short("2026") == "2026"
