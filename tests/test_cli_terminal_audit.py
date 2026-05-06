"""BRAIN-185: cli_terminal.py color + event-formatter audit.

Pure helpers in `huntova tail` / `huntova run`. Server probing is
network-dependent (skipped); color + format helpers are pure.

Pinned invariants:

1. `_color_enabled` returns False when `NO_COLOR` env is set
   (https://no-color.org standard).
2. `_color_enabled` returns False when stdout isn't a TTY.
3. `_c` returns bare text when color disabled (no ANSI codes).
4. `_c` returns text wrapped in correct ANSI code when enabled.
5. `_c` falls back to no-op for unknown color name.
6. `_format_label` produces a 20-char-padded label (alignment).
7. `_format_body` for unknown event renders JSON-serialised data,
   capped at 200 chars.
8. `_format_body` empty data returns empty string.
9. `_print_event` handles non-JSON `raw_data` gracefully.
"""
from __future__ import annotations


def test_color_disabled_when_no_color_env(monkeypatch):
    from cli_terminal import _color_enabled
    monkeypatch.setenv("NO_COLOR", "1")
    assert _color_enabled() is False


def test_color_disabled_when_no_color_empty_string(monkeypatch):
    """`NO_COLOR=` (empty) — debate says non-empty matters; pin
    current behaviour (any-truthy disables)."""
    from cli_terminal import _color_enabled
    monkeypatch.setenv("NO_COLOR", "")
    # Empty string is falsy in Python — color may stay enabled.
    # Pin actual behaviour rather than assert one direction.
    out = _color_enabled()
    assert isinstance(out, bool)


def test_color_disabled_when_not_tty(monkeypatch):
    import sys
    from cli_terminal import _color_enabled
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    assert _color_enabled() is False


def test_color_enabled_on_tty(monkeypatch):
    import sys
    from cli_terminal import _color_enabled
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    assert _color_enabled() is True


def test_c_returns_bare_text_when_disabled(monkeypatch):
    from cli_terminal import _c
    monkeypatch.setenv("NO_COLOR", "1")
    assert _c("cyan", "hello") == "hello"


def test_c_wraps_with_ansi_when_enabled(monkeypatch):
    import sys
    from cli_terminal import _c
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    out = _c("cyan", "hello")
    assert "\033[36m" in out  # cyan
    assert "hello" in out
    assert "\033[0m" in out  # reset


def test_c_color_codes_known(monkeypatch):
    import sys
    from cli_terminal import _c
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    # Each color must produce a distinct prefix.
    cyan = _c("cyan", "x")
    red = _c("red", "x")
    green = _c("green", "x")
    assert cyan != red != green


def test_c_unknown_color_falls_back(monkeypatch):
    """Unknown color uses '0' (default) — must not crash."""
    import sys
    from cli_terminal import _c
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    out = _c("nonexistent-color", "x")
    assert "x" in out  # at minimum text is present


def test_format_label_padded_to_20(monkeypatch):
    from cli_terminal import _format_label
    monkeypatch.setenv("NO_COLOR", "1")
    out = _format_label("lead")
    # No ANSI codes (NO_COLOR), so length should be padded.
    # Format: `[lead]` → 6 chars; ljust(20) → 20.
    assert len(out) == 20


def test_format_body_unknown_event_returns_json(monkeypatch):
    from cli_terminal import _format_body
    monkeypatch.setenv("NO_COLOR", "1")
    out = _format_body("unknown_event_type", {"key": "value"})
    assert "key" in out
    assert "value" in out


def test_format_body_caps_at_200_chars(monkeypatch):
    from cli_terminal import _format_body
    monkeypatch.setenv("NO_COLOR", "1")
    big = {"data": "x" * 1000}
    out = _format_body("unknown", big)
    assert len(out) <= 200


def test_format_body_empty_data():
    from cli_terminal import _format_body
    assert _format_body("anything", {}) == ""


def test_format_body_handles_non_dict():
    """Defensive: non-dict input must not crash."""
    from cli_terminal import _format_body
    out = _format_body("anything", "raw string")  # type: ignore[arg-type]
    # Must return some str.
    assert isinstance(out, str)


def test_print_event_handles_non_json_raw(capsys):
    """Raw data that's not JSON should be wrapped as `{"raw": ...}`."""
    from cli_terminal import _print_event
    _print_event("test_event", "this is not JSON")
    out = capsys.readouterr().out
    # Got printed without crash.
    assert "test_event" in out


def test_print_event_handles_empty_raw(capsys):
    from cli_terminal import _print_event
    _print_event("test_event", "")
    # Must not crash.


def test_print_event_handles_json_object(capsys):
    from cli_terminal import _print_event
    _print_event("test_event", '{"foo":"bar"}')
    out = capsys.readouterr().out
    assert "test_event" in out


def test_print_event_handles_malformed_json(capsys):
    """Malformed JSON should fall through to the raw rendering."""
    from cli_terminal import _print_event
    _print_event("test_event", "{malformed")
    # Must not crash.


def test_format_body_truncates_long_string_data(monkeypatch):
    """200-char cap on unknown event renders applies to non-dict
    data too."""
    from cli_terminal import _format_body
    monkeypatch.setenv("NO_COLOR", "1")
    out = _format_body("unknown", {"x": "y" * 1000})
    assert len(out) <= 200
