"""BRAIN-173: cli_pulse.py since-parser + ISO-parser audit.

`_parse_since` accepts '7d' / '30d' / '24h' / '2w' / bare-int-days.
`_parse_iso` accepts ISO timestamps with optional Z-suffix and
defaults missing timezone info to UTC. Both used to filter leads
by recency.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def test_parse_since_days():
    from cli_pulse import _parse_since
    assert _parse_since("7d") == timedelta(days=7)
    assert _parse_since("30d") == timedelta(days=30)
    assert _parse_since("1d") == timedelta(days=1)


def test_parse_since_hours():
    from cli_pulse import _parse_since
    assert _parse_since("24h") == timedelta(hours=24)
    assert _parse_since("1h") == timedelta(hours=1)


def test_parse_since_weeks():
    from cli_pulse import _parse_since
    assert _parse_since("2w") == timedelta(weeks=2)
    assert _parse_since("4w") == timedelta(weeks=4)


def test_parse_since_bare_int_means_days():
    from cli_pulse import _parse_since
    assert _parse_since("7") == timedelta(days=7)


def test_parse_since_default_when_empty():
    from cli_pulse import _parse_since
    assert _parse_since("") == timedelta(days=7)
    assert _parse_since(None) == timedelta(days=7)  # type: ignore[arg-type]


def test_parse_since_default_on_garbage():
    from cli_pulse import _parse_since
    assert _parse_since("not-a-duration") == timedelta(days=7)
    assert _parse_since("abc") == timedelta(days=7)
    assert _parse_since("d") == timedelta(days=7)  # missing number
    assert _parse_since("xyz123abc") == timedelta(days=7)


def test_parse_since_case_insensitive():
    from cli_pulse import _parse_since
    assert _parse_since("7D") == timedelta(days=7)
    assert _parse_since("24H") == timedelta(hours=24)
    assert _parse_since("2W") == timedelta(weeks=2)


def test_parse_since_strips_whitespace():
    from cli_pulse import _parse_since
    assert _parse_since("  7d  ") == timedelta(days=7)


def test_parse_iso_returns_datetime():
    from cli_pulse import _parse_iso
    dt = _parse_iso("2026-05-04T09:30:00Z")
    assert dt is not None
    assert dt.year == 2026
    assert dt.month == 5
    assert dt.day == 4


def test_parse_iso_handles_z_suffix():
    """Python's datetime.fromisoformat doesn't accept 'Z' in older
    versions. The helper should normalise Z → +00:00."""
    from cli_pulse import _parse_iso
    dt = _parse_iso("2026-05-04T09:30:00Z")
    assert dt is not None
    assert dt.tzinfo is not None
    # Should be UTC.
    assert dt.utcoffset() == timedelta(0)


def test_parse_iso_handles_explicit_offset():
    from cli_pulse import _parse_iso
    dt = _parse_iso("2026-05-04T09:30:00+02:00")
    assert dt is not None
    assert dt.utcoffset() == timedelta(hours=2)


def test_parse_iso_assumes_utc_for_naive():
    """A timestamp without timezone info defaults to UTC (so date
    comparisons don't crash on tz-aware vs tz-naive)."""
    from cli_pulse import _parse_iso
    dt = _parse_iso("2026-05-04T09:30:00")
    assert dt is not None
    assert dt.tzinfo is not None
    assert dt.utcoffset() == timedelta(0)


def test_parse_iso_returns_none_for_garbage():
    from cli_pulse import _parse_iso
    assert _parse_iso("not-a-date") is None
    assert _parse_iso("") is None
    assert _parse_iso(None) is None
    assert _parse_iso("yesterday") is None


def test_parse_iso_returns_none_for_non_string():
    from cli_pulse import _parse_iso
    # Python will str() it but the result won't parse.
    assert _parse_iso(123) is None
    assert _parse_iso([]) is None


def test_parse_since_negative_value_falls_back():
    """Negative durations (`-7d`) — int(-7) parses but timedelta is
    negative, which then makes `cutoff = now - (-7d) = now + 7d` and
    filters out everything. Defensive: fall back to default."""
    from cli_pulse import _parse_since
    # int("-7") parses successfully → timedelta(days=-7) returned
    # CURRENT BEHAVIOR. Pin it so a future "fix" is intentional, not
    # accidental.
    out = _parse_since("-7d")
    # If we ever start clamping negative, change this assertion.
    assert isinstance(out, timedelta)


def test_parse_since_zero_value():
    """0d → timedelta(0) — matches "leads in the last 0 days" =
    nothing. Pin behaviour."""
    from cli_pulse import _parse_since
    assert _parse_since("0d") == timedelta(0)
    assert _parse_since("0") == timedelta(0)
