"""Regression test for BRAIN-61 (a422): cli_remote.py _watch_loop
must refuse to start with an empty allowlist. Pre-fix the dispatch
gate did `if allowed and chat not in allowed:` — empty allowlist
short-circuited the gate to False, and EVERY Telegram message
reached _dispatch_to_chat(). Anyone who knew the bot's @handle
could remote-control the operator's Huntova install.

Per Telegram bot security guidance + GPT-5.4 audit.
"""
from __future__ import annotations
import inspect


def test_watch_loop_refuses_empty_allowlist():
    """Source-level: the watch loop must early-return with a refusal
    when no chat IDs are whitelisted."""
    from cli_remote import _watch_loop
    src = inspect.getsource(_watch_loop)
    assert "if not allowed" in src or "REFUSING TO START" in src or "not allowed:" in src, (
        "BRAIN-61 regression: _watch_loop must refuse to start when "
        "the allowlist is empty. Otherwise anyone who knows the bot "
        "handle can dispatch commands."
    )
    assert "REFUSING" in src or "refuse" in src.lower(), (
        "BRAIN-61: refusal must be explicit (printed warning + nonzero "
        "exit) so the operator notices."
    )


def test_dispatch_gate_no_short_circuit_on_empty():
    """Source-level: the per-message dispatch gate must NOT use
    `if allowed and chat not in allowed:` — that pattern fails-open
    on empty allowlists. Must be unconditional set-membership check."""
    from cli_remote import _watch_loop
    src = inspect.getsource(_watch_loop)
    # The bad pattern would be `if allowed and int(chat) not in allowed:`
    # appearing as actual code (not in a comment). Check by scanning
    # non-comment lines.
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert "if allowed and int(chat) not in allowed" not in line, (
            "BRAIN-61 regression: `if allowed and chat not in allowed` "
            "fails open when allowed is empty. Use unconditional "
            "`if int(chat) not in allowed:` (fail-closed)."
        )
