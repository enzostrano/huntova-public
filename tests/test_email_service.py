"""Test the header-injection defences in email_service.

a289 added `_scrub_header` to defend against AI-generated subjects that
contain CRLF + control bytes. Round 2 audit (a292) added byte-cap. The
audit at a307 found ZERO tests covering this critical surface; this
file closes that gap.
"""

import pytest


def test_scrub_header_strips_crlf():
    """Hostile subjects with embedded CRLF must not survive."""
    from email_service import _scrub_header
    hostile = "Hello\r\nBcc: attacker@example.com"
    out = _scrub_header(hostile)
    assert "\r" not in out
    assert "\n" not in out
    assert "Bcc:" in out  # the literal token survives, but as inline text
    # The injection vector — having a SECOND header line — is dead because
    # the header has been collapsed to one line.
    assert out.count("\n") == 0


def test_scrub_header_strips_control_bytes_keeps_tab():
    """C0 control bytes (\\x00-\\x1F) get stripped, except TAB."""
    from email_service import _scrub_header
    s = "abc\x00def\x07ghi\tjkl"
    out = _scrub_header(s)
    assert "\x00" not in out
    assert "\x07" not in out
    assert "\t" in out  # tab preserved
    assert "abc" in out and "def" in out and "ghi" in out and "jkl" in out


def test_scrub_header_byte_caps_at_998():
    """RFC 5322 caps a header line at 998 octets — we enforce in bytes,
    not codepoints, so multi-byte UTF-8 doesn't sneak past."""
    from email_service import _scrub_header
    # ASCII case: 1500 chars → 998 bytes
    long_ascii = "a" * 1500
    out = _scrub_header(long_ascii)
    assert len(out.encode("utf-8")) <= 998

    # Multi-byte UTF-8: each ä = 2 bytes. 600 ä codepoints = 1200 bytes.
    long_utf = "ä" * 600
    out = _scrub_header(long_utf)
    assert len(out.encode("utf-8")) <= 998
    # Decode-with-errors='ignore' should drop trailing partial sequences
    # cleanly — the result is always valid UTF-8.
    out.encode("utf-8")  # won't raise


def test_scrub_header_handles_none():
    """None should return empty string, not raise."""
    from email_service import _scrub_header
    assert _scrub_header(None) == ""
    assert _scrub_header("") == ""


def test_smtp_rate_limited_error_class():
    """Verify the typed exception is reachable + carries info."""
    from email_service import SMTPRateLimitedError
    e = SMTPRateLimitedError("hourly cap reached (30/h) — retry in ~120s")
    assert isinstance(e, RuntimeError)
    assert "30/h" in str(e)


def test_smtp_delivery_error_carries_code_and_perm_flag():
    """SMTPDeliveryError exposes code + permanent so callers can
    distinguish bounce from defer."""
    from email_service import SMTPDeliveryError
    e = SMTPDeliveryError(550, "Mailbox does not exist", permanent=True)
    assert e.code == 550
    assert e.permanent is True
    assert "550" in str(e)
    assert "Mailbox" in str(e)

    soft = SMTPDeliveryError(421, "Service not available", permanent=False)
    assert soft.permanent is False
    assert soft.code == 421
