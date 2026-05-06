"""BRAIN-168: tui.py invariant + sanitization audit.

The TUI primitives are user-facing first-impression code: ANSI
helpers, banner, spinners, browser launcher. These tests pin:

1. No AI-author mentions (Claude / GPT / "AI built using AI") in
   shipped taglines per the no-AI-mentions standing order.
2. ANSI helpers strip color when stdout isn't a TTY (otherwise log
   files get polluted with escape codes).
3. `open_url` rejects non-http(s) schemes (no `file://` / `javascript:`).
4. `open_url` honours `HV_NO_BROWSER` and `CI` env vars.
5. `detect_browser_open_support` correctly identifies SSH-without-
   display.
6. ASCII banner uses only ASCII chars (so a non-UTF terminal
   doesn't render mojibake).
"""
from __future__ import annotations

import importlib


def test_no_ai_authorship_mentions_in_taglines():
    """Per Enzo standing order 2026-05-03 (load-bearing): no
    Claude/GPT/AI-author mentions in shipped artifacts. Taglines are
    user-facing copy."""
    import tui
    importlib.reload(tui)
    forbidden_phrases = [
        # Phrases that reveal AI authorship rather than just naming a
        # supported provider.
        "irony of an ai built",
        "built using ai",
        "built with claude",
        "built using claude",
        "made by claude",
        "claude wrote",
        "gpt wrote",
    ]
    for tagline in tui._TAGLINES:
        lower = tagline.lower()
        for phrase in forbidden_phrases:
            assert phrase not in lower, (
                f"tagline reveals AI authorship: {tagline!r} "
                f"(forbidden phrase {phrase!r})"
            )


def test_ansi_strips_color_when_not_tty(monkeypatch):
    import tui
    importlib.reload(tui)
    # Force isatty False.
    import sys
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    # All color helpers must return the bare string.
    assert tui.bold("hello") == "hello"
    assert tui.red("oops") == "oops"
    assert tui.green("ok") == "ok"
    assert tui.cyan("info") == "info"


def test_ansi_applies_color_when_tty(monkeypatch):
    import tui
    importlib.reload(tui)
    import sys
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    assert "\033[1m" in tui.bold("x")
    assert "\033[31m" in tui.red("x")
    assert "\033[32m" in tui.green("x")
    assert "\033[0m" in tui.bold("x")


def test_open_url_rejects_non_http_scheme():
    import tui
    importlib.reload(tui)
    # Even when can_open=True, non-http schemes must reject.
    assert tui.open_url("file:///etc/passwd") is False
    assert tui.open_url("javascript:alert(1)") is False
    assert tui.open_url("ftp://example.com/") is False
    assert tui.open_url("") is False


def test_open_url_honours_hv_no_browser(monkeypatch):
    import tui
    importlib.reload(tui)
    monkeypatch.setenv("HV_NO_BROWSER", "1")
    # Even with a valid http URL.
    assert tui.open_url("http://localhost:5050/") is False


def test_open_url_honours_ci_env(monkeypatch):
    import tui
    importlib.reload(tui)
    monkeypatch.delenv("HV_NO_BROWSER", raising=False)
    monkeypatch.setenv("CI", "true")
    assert tui.open_url("http://localhost:5050/") is False


def test_detect_browser_open_support_ssh_no_display(monkeypatch):
    """SSH without DISPLAY → cannot open browser."""
    import tui
    importlib.reload(tui)
    monkeypatch.setenv("SSH_CONNECTION", "1.2.3.4 5678 5.6.7.8 22")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(tui.platform, "system", lambda: "Linux")
    can, reason = tui.detect_browser_open_support()
    assert can is False
    assert reason == "ssh-no-display"


def test_banner_is_ascii_only():
    """The banner string must be plain ASCII so non-UTF terminals
    (rare but exists) don't render it as mojibake."""
    import tui
    importlib.reload(tui)
    # Only ASCII codepoints (0-127).
    for ch in tui.HUNTOVA_BANNER:
        assert ord(ch) < 128, (
            f"banner char {ch!r} (U+{ord(ch):04X}) is non-ASCII"
        )


def test_taglines_are_strings_and_nonempty():
    import tui
    importlib.reload(tui)
    assert len(tui._TAGLINES) >= 5, "should have a handful of taglines"
    for t in tui._TAGLINES:
        assert isinstance(t, str)
        assert t.strip(), "tagline must not be blank"
        assert len(t) <= 200, f"tagline too long for one line: {t!r}"


def test_pick_tagline_returns_one_of_the_set():
    import tui
    importlib.reload(tui)
    for _ in range(20):
        t = tui._pick_tagline()
        assert t in tui._TAGLINES


def test_open_url_no_scheme_rejected():
    """A bare hostname like 'localhost:5050' (no scheme) must reject."""
    import tui
    importlib.reload(tui)
    assert tui.open_url("localhost:5050") is False
    assert tui.open_url("example.com") is False


def test_spinner_falls_back_when_not_tty(monkeypatch, capsys):
    """Spinner must print a single line + skip animation on non-TTY
    so log files don't get carriage-return spam."""
    import tui
    importlib.reload(tui)
    monkeypatch.setattr(tui, "_is_tty", lambda: False)
    sp = tui.Spinner("loading…")
    sp.start()
    sp.stop(message="done", success=True)
    out = capsys.readouterr().out
    # No carriage-return-based animation.
    assert "\r" not in out, "Spinner must not emit \\r on non-TTY"
