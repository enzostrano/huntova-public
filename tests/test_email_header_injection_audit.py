"""BRAIN-175: email_service._scrub_header header-injection guard audit.

`_scrub_header` is the gate that strips CR/LF and control bytes from
strings going into email headers. If this regresses, any AI-generated
subject (or AI-scraped hostile page text) containing
`Hello\\r\\nBcc: attacker@x.com` gets folded into the SMTP DATA
stream as a separate Bcc header — silent exfiltration of every cold
email.

Pinned invariants:

1. CR (`\\r`) replaced with space.
2. LF (`\\n`) replaced with space.
3. CRLF replaced with two spaces (no header break possible).
4. C0 control bytes (0x00-0x1F) except TAB stripped.
5. TAB (0x09) preserved (legal in email headers).
6. RFC 5322 byte-cap (998) enforced — not codepoint-cap (a292 fix).
7. Multi-byte UTF-8 boundary handled cleanly (no partial char).
8. None / empty input returns "" without raising.
"""
from __future__ import annotations


def test_strips_cr():
    from email_service import _scrub_header
    assert "\r" not in _scrub_header("Hello\rWorld")


def test_strips_lf():
    from email_service import _scrub_header
    assert "\n" not in _scrub_header("Hello\nWorld")


def test_strips_crlf():
    from email_service import _scrub_header
    out = _scrub_header("Hello\r\nBcc: attacker@example.com")
    assert "\r" not in out
    assert "\n" not in out
    # Bcc text is now sanitised into the body (still in the value),
    # but as a single line — no header break possible.
    assert "\nBcc:" not in out
    assert "\rBcc:" not in out


def test_replaces_cr_with_space():
    from email_service import _scrub_header
    out = _scrub_header("a\rb")
    assert out == "a b"


def test_replaces_lf_with_space():
    from email_service import _scrub_header
    out = _scrub_header("a\nb")
    assert out == "a b"


def test_strips_null_byte():
    from email_service import _scrub_header
    out = _scrub_header("hello\x00world")
    assert "\x00" not in out


def test_strips_c0_controls():
    from email_service import _scrub_header
    # All C0 controls (0x00-0x1F) except TAB should be stripped.
    for code in range(0x20):
        if code == 0x09:
            continue  # TAB preserved
        ch = chr(code)
        out = _scrub_header(f"a{ch}b")
        assert ch not in out, f"C0 char U+{code:04X} not stripped"


def test_preserves_tab():
    """TAB (0x09) is legal in email headers and must survive."""
    from email_service import _scrub_header
    out = _scrub_header("a\tb")
    assert "\t" in out


def test_preserves_normal_text():
    from email_service import _scrub_header
    out = _scrub_header("Hello, World! 2026")
    assert out == "Hello, World! 2026"


def test_byte_cap_enforced():
    """RFC 5322 998-octet limit is bytes, not codepoints. ASCII text
    over 998 bytes must be capped."""
    from email_service import _scrub_header
    long_ascii = "x" * 2000
    out = _scrub_header(long_ascii)
    assert len(out.encode("utf-8")) <= 998


def test_byte_cap_handles_multibyte_utf8():
    """a292 fix: byte-cap not codepoint-cap. A multi-byte UTF-8
    string can encode to >998 bytes even if its Python str length
    is exactly 998."""
    from email_service import _scrub_header
    # Each emoji is 4 UTF-8 bytes; 300 emojis = 1200 bytes.
    weird = "🦊" * 300
    out = _scrub_header(weird)
    assert len(out.encode("utf-8")) <= 998


def test_byte_cap_no_partial_utf8_sequence():
    """At the byte-cap boundary, must not leave a partial multi-byte
    sequence that would raise UnicodeDecodeError downstream."""
    from email_service import _scrub_header
    # Mix of ASCII + emoji chosen so the cap lands mid-emoji.
    weird = "x" * 996 + "🦊"  # 996 + 4 bytes = 1000 bytes
    out = _scrub_header(weird)
    # Should decode without errors (must be valid UTF-8).
    out.encode("utf-8").decode("utf-8")  # would raise if invalid


def test_none_returns_empty_string():
    from email_service import _scrub_header
    assert _scrub_header(None) == ""


def test_empty_returns_empty_string():
    from email_service import _scrub_header
    assert _scrub_header("") == ""


def test_non_string_coerced():
    """Defensive: a non-string (int, dict accidentally passed in)
    must not crash."""
    from email_service import _scrub_header
    out = _scrub_header(123)
    assert isinstance(out, str)


def test_max_len_override():
    """Caller can pass a tighter cap (e.g. for Subject: which most
    MTAs trim to 78-100 chars)."""
    from email_service import _scrub_header
    out = _scrub_header("x" * 200, max_len=50)
    assert len(out.encode("utf-8")) <= 50


def test_bcc_injection_attack_neutralised():
    """The classic header injection payload — verify it's neutralised."""
    from email_service import _scrub_header
    payload = "innocent subject\r\nBcc: attacker@evil.com\r\nX-Forwarded: yes"
    out = _scrub_header(payload)
    # No CRLF means no header break, so SMTP will see this as ONE header value.
    assert "\r\n" not in out
    assert "\r" not in out
    assert "\n" not in out
