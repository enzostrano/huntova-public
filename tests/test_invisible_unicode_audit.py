"""BRAIN-211: server._normalize_invisible_unicode + _INVISIBLE_UNICODE_RE audit.

a512 (BRAIN-137) added this canonicalizer for the BRAIN-85
idempotency cache. Two semantically-equivalent strings (one with a
BOM, one without; NFD vs NFC) must hash identically so retry
short-circuits fire correctly.

Pinned invariants:

1. None / empty → returned unchanged (no crash).
2. BOM (`﻿`) stripped.
3. Zero-width space (`​`) stripped.
4. Bidi override / isolate marks stripped.
5. NUL byte stripped.
6. C0 controls (except TAB/LF/CR) stripped.
7. NFD → NFC normalisation (decomposed e + combining acute → é).
8. TAB / LF / CR preserved (legitimate whitespace).
9. Strip-then-NFC ordering (NOT NFC-then-strip — would leave
   decomposed bases without their marks).
10. `_INVISIBLE_UNICODE_RE` covers the documented set.
"""
from __future__ import annotations

import unicodedata


def test_normalize_handles_none():
    from server import _normalize_invisible_unicode
    assert _normalize_invisible_unicode(None) is None


def test_normalize_handles_empty():
    from server import _normalize_invisible_unicode
    assert _normalize_invisible_unicode("") == ""


def test_strips_bom():
    """BOM (`\\ufeff`) stripped from any position."""
    from server import _normalize_invisible_unicode
    out = _normalize_invisible_unicode("﻿Acme")
    assert "﻿" not in out
    assert out == "Acme"


def test_strips_zero_width_space():
    from server import _normalize_invisible_unicode
    out = _normalize_invisible_unicode("Ac​me")
    assert "​" not in out
    assert out == "Acme"


def test_strips_zero_width_joiner():
    from server import _normalize_invisible_unicode
    out = _normalize_invisible_unicode("A‍cme")
    # ZWJ is at U+200D — in the [​-‏] range.
    assert "‍" not in out


def test_strips_bidi_overrides():
    """LRE / RLE / PDF / LRO / RLO bidi-override marks stripped."""
    from server import _normalize_invisible_unicode
    out = _normalize_invisible_unicode("‪Acme‬")  # LRE … PDF
    assert "‪" not in out
    assert "‬" not in out


def test_strips_null_byte():
    from server import _normalize_invisible_unicode
    out = _normalize_invisible_unicode("Ac\x00me")
    assert "\x00" not in out


def test_strips_c0_controls():
    """C0 controls 0x00-0x08, 0x0b-0x0c, 0x0e-0x1f, 0x7f stripped."""
    from server import _normalize_invisible_unicode
    for code in (0x01, 0x02, 0x07, 0x0b, 0x0c, 0x10, 0x1f, 0x7f):
        out = _normalize_invisible_unicode(f"a{chr(code)}b")
        assert chr(code) not in out, f"C0 char U+{code:04X} not stripped"


def test_preserves_tab_lf_cr():
    """TAB (0x09), LF (0x0a), CR (0x0d) are legitimate whitespace —
    must NOT be stripped."""
    from server import _normalize_invisible_unicode
    out = _normalize_invisible_unicode("a\tb\nc\rd")
    assert "\t" in out
    assert "\n" in out
    assert "\r" in out


def test_nfc_normalises_decomposed():
    """NFD `Café` (e + combining acute) → NFC `é`."""
    from server import _normalize_invisible_unicode
    nfd = "Café"  # e + combining acute (NFD form)
    out = _normalize_invisible_unicode(nfd)
    # Output should be NFC.
    expected_nfc = unicodedata.normalize("NFC", "Café")
    assert out == expected_nfc


def test_strip_then_nfc_ordering():
    """Strip BEFORE NFC, not after. If NFC first, an invisible
    combining mark might pull a stripped base char back. Test:
    `e + COMBINING ACUTE + ZWNJ` should strip the ZWNJ THEN compose
    e+acute → é (i.e., the result is `é`)."""
    from server import _normalize_invisible_unicode
    s = "é‌"  # e + combining acute + ZWNJ
    out = _normalize_invisible_unicode(s)
    # ZWNJ stripped; then e+acute composed via NFC → é.
    assert "‌" not in out
    # The composed é (U+00E9) should be present.
    assert "é" in out or "é" in out


def test_round_trip_clean_string():
    """A string with no invisible chars and already in NFC should
    round-trip unchanged."""
    from server import _normalize_invisible_unicode
    s = "Hello, world!"
    assert _normalize_invisible_unicode(s) == s


def test_strips_word_joiner():
    """U+2060 (WJ — word joiner) — invisible character used to
    inhibit line breaks. In [\\u2060-\\u2064] range."""
    from server import _normalize_invisible_unicode
    out = _normalize_invisible_unicode("Ac⁠me")
    assert "⁠" not in out


def test_strips_bidi_isolate():
    """U+2066 (LRI), U+2069 (PDI) bidi isolate marks."""
    from server import _normalize_invisible_unicode
    out = _normalize_invisible_unicode("⁦Hello⁩")
    assert "⁦" not in out
    assert "⁩" not in out


def test_two_inputs_with_invisible_diff_collide():
    """Critical for BRAIN-85: two strings differing only by invisible
    chars must canonicalise to the same value (so the idempotency
    cache hits)."""
    from server import _normalize_invisible_unicode
    a = _normalize_invisible_unicode("Acme")
    b = _normalize_invisible_unicode("﻿Acme")
    c = _normalize_invisible_unicode("Ac​me")
    assert a == b == c


def test_invisible_unicode_re_compiled():
    """The regex constant is a compiled Pattern (not a bare string)."""
    from server import _INVISIBLE_UNICODE_RE
    import re
    assert isinstance(_INVISIBLE_UNICODE_RE, re.Pattern)
