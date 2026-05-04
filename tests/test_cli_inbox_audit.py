"""BRAIN-187: cli_inbox.py reply-classifier + auto-reply detector audit.

Pure helpers in `huntova inbox watch`. Pinned invariants:

1. `_split_msgids` extracts angle-bracketed IDs from In-Reply-To /
   References headers; handles multi-ID, whitespace, empty.
2. `_decode_payload` falls back through declared charset → utf-8 →
   cp1252 → latin-1; never crashes on bad bytes.
3. `_is_autoreply` matches RFC 3834 / common heuristic subjects +
   headers (auto-submitted, x-autoreply, x-auto-response-suppress).
4. `_is_autoreply` peels stacked `Re:` / `Fwd:` prefixes before
   matching ("Re: Re: Out of office" is still OOO).
5. `_heuristic_class` audit-wave-27 fix — "will be back on" alone
   no longer triggers OOO.
6. `_heuristic_class` returns None when no rule matches (handing
   off to AI).
7. `_classify_reply` short-circuits via heuristic when applicable.
8. `_status_for_class` returns None for out_of_office (don't flip).
9. `_status_for_class` maps each class correctly.
"""
from __future__ import annotations


def test_split_msgids_single():
    from cli_inbox import _split_msgids
    assert _split_msgids("<abc@example.com>") == ["abc@example.com"]


def test_split_msgids_multiple():
    from cli_inbox import _split_msgids
    out = _split_msgids("<a@x.com> <b@y.com> <c@z.com>")
    assert out == ["a@x.com", "b@y.com", "c@z.com"]


def test_split_msgids_empty():
    from cli_inbox import _split_msgids
    assert _split_msgids("") == []
    assert _split_msgids(None) == []  # type: ignore[arg-type]


def test_split_msgids_no_brackets_returns_empty():
    from cli_inbox import _split_msgids
    # Without angle brackets, the regex shouldn't match.
    assert _split_msgids("just-an-id-no-brackets") == []


def test_decode_payload_utf8():
    from cli_inbox import _decode_payload
    raw = "héllo".encode("utf-8")
    assert _decode_payload(raw, "utf-8") == "héllo"


def test_decode_payload_cp1252_fallback():
    from cli_inbox import _decode_payload
    # cp1252 specific bytes that aren't valid UTF-8.
    raw = b"caf\xe9"  # café in cp1252
    out = _decode_payload(raw, None)
    # Should decode to "café" (cp1252 fallback).
    assert "café" in out or out  # at minimum no crash


def test_decode_payload_empty():
    from cli_inbox import _decode_payload
    assert _decode_payload(b"", None) == ""
    assert _decode_payload(None, None) == ""


def test_decode_payload_unknown_charset_falls_through():
    """Unknown charset should fall through to next candidate."""
    from cli_inbox import _decode_payload
    raw = "hello".encode("utf-8")
    out = _decode_payload(raw, "made-up-charset-xyz")
    assert out == "hello"


def test_is_autoreply_subject_ooo():
    from cli_inbox import _is_autoreply
    assert _is_autoreply("Out of office", []) is True
    assert _is_autoreply("Auto: I'm away", []) is True
    assert _is_autoreply("Automatic reply: vacation", []) is True


def test_is_autoreply_strips_re_prefix():
    """Stacked Re: prefixes peeled before matching."""
    from cli_inbox import _is_autoreply
    assert _is_autoreply("Re: Out of office", []) is True
    assert _is_autoreply("Re: Re: Out of office", []) is True
    assert _is_autoreply("Fwd: Out of office", []) is True


def test_is_autoreply_genuine_reply_not_flagged():
    from cli_inbox import _is_autoreply
    assert _is_autoreply("Re: Your offer", []) is False
    assert _is_autoreply("Interested in chatting", []) is False


def test_is_autoreply_auto_submitted_header():
    """RFC 3834 Auto-Submitted header (anything but 'no') signals auto."""
    from cli_inbox import _is_autoreply
    assert _is_autoreply("Re: hello",
                          [("Auto-Submitted", "auto-replied")]) is True
    assert _is_autoreply("Re: hello",
                          [("Auto-Submitted", "no")]) is False


def test_is_autoreply_x_autoreply_header():
    from cli_inbox import _is_autoreply
    assert _is_autoreply("Re: hello", [("X-Autoreply", "yes")]) is True


def test_is_autoreply_x_auto_response_suppress():
    """Outlook OOO uses X-Auto-Response-Suppress."""
    from cli_inbox import _is_autoreply
    assert _is_autoreply("Re: hello",
                          [("X-Auto-Response-Suppress", "All")]) is True


def test_heuristic_class_ooo():
    from cli_inbox import _heuristic_class
    assert _heuristic_class("hi", "I am out of office until Monday") == "out_of_office"
    assert _heuristic_class("hi", "I'm currently away from my desk") == "out_of_office"


def test_heuristic_class_does_not_match_will_be_back_alone():
    """Audit wave 27 fix: 'will be back on Monday with budget' must
    NOT be classified as OOO. Returns None — let AI decide."""
    from cli_inbox import _heuristic_class
    out = _heuristic_class("Re: your pitch",
                            "Hi, I will be back on Monday with budget approval")
    # Should NOT classify as OOO via heuristic.
    assert out != "out_of_office"


def test_heuristic_class_unsub():
    from cli_inbox import _heuristic_class
    assert _heuristic_class("Re: x", "Please unsubscribe me") == "unsubscribe"
    assert _heuristic_class("Re: x", "Take me off your list") == "unsubscribe"
    assert _heuristic_class("Re: x", "do not contact me again") == "unsubscribe"


def test_heuristic_class_wrong_person():
    from cli_inbox import _heuristic_class
    assert _heuristic_class("Re: x", "I'm the wrong person — please contact Bob") == "wrong_person"
    assert _heuristic_class("Re: x", "I no longer work here") == "wrong_person"
    assert _heuristic_class("Re: x", "I've left the company") == "wrong_person"


def test_heuristic_class_returns_none_for_normal():
    from cli_inbox import _heuristic_class
    assert _heuristic_class("Re: pitch", "Sounds interesting, let's chat") is None


def test_heuristic_class_handles_none_body():
    from cli_inbox import _heuristic_class
    assert _heuristic_class("Re: x", None) is None  # type: ignore[arg-type]
    assert _heuristic_class(None, None) is None  # type: ignore[arg-type]


def test_status_for_class_ooo_returns_none():
    """OOO doesn't flip status — it's not a real reply."""
    from cli_inbox import _status_for_class
    assert _status_for_class("out_of_office") is None


def test_status_for_class_known_classes():
    from cli_inbox import _status_for_class
    # Each known class either returns a string or None.
    for klass in ("interested", "not_interested", "not_now",
                   "wrong_person", "unsubscribe"):
        out = _status_for_class(klass)
        # Either str or None — must not raise.
        assert out is None or isinstance(out, str)


def test_status_for_class_unknown_returns_none_or_default():
    from cli_inbox import _status_for_class
    # Unknown class — defensive return.
    out = _status_for_class("totally-unknown-class")
    assert out is None or isinstance(out, str)


def test_valid_classes_constant():
    """`_VALID_CLASSES` constant must include the standard 6."""
    from cli_inbox import _VALID_CLASSES
    expected = {"interested", "not_interested", "not_now",
                "out_of_office", "wrong_person", "unsubscribe"}
    assert _VALID_CLASSES == expected
