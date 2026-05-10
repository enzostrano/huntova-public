"""Regression test for BRAIN-66 (a427): cli_inbox.py reply matching
must NOT use the From-address fallback. SMTP From is spoofable;
the only authenticated correlation between an inbound message
and one of our outbound sends is In-Reply-To / References
matching our random per-send Message-ID.

Per GPT-5.4 audit on email-spoofing class.
"""
from __future__ import annotations
import inspect


def test_scan_inbox_dropped_from_address_fallback():
    """Source-level: the From-address fallback that mapped by_email
    onto a lead must be removed."""
    import cli_inbox
    src = inspect.getsource(cli_inbox._scan_inbox)
    # The bad pattern: `if not lid and from_addr in by_email:`
    bad_pattern_a = "from_addr in by_email"
    bad_pattern_b = "lid = by_email[from_addr]"
    # Allow these strings only inside comments (the BRAIN-66 fix
    # comment explains the prior pattern). Check non-comment lines.
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert bad_pattern_a not in line, (
            "BRAIN-66 regression: From-address fallback removed; "
            "SMTP From spoofing → fake replies pollute lead status."
        )
        assert bad_pattern_b not in line, (
            "BRAIN-66 regression: by_email lookup must not flow into "
            "lead-id assignment."
        )


def test_scan_inbox_still_uses_threading_headers():
    """Don't regress the proper Message-ID threading binding."""
    import cli_inbox
    src = inspect.getsource(cli_inbox._scan_inbox)
    assert "In-Reply-To" in src and "by_msgid" in src, (
        "BRAIN-66 regression: threading-header match must remain — "
        "it's the only authenticated correlation."
    )
