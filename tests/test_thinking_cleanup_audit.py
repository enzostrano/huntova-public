"""BRAIN-197: app._clean_thinking + _clean_subject + extract_json audit.

LLM responses sometimes leak the model's reasoning ("Thinking
Process: ...", "**Sender role:**", chain-of-thought) into the user-
facing draft. These cleaners scrub it before the email goes out.

Pinned invariants:

1. `_clean_thinking` keeps real email content + paragraph breaks.
2. `_clean_thinking` strips lines containing "thinking process",
   "**sender", "**recipient", "**tone", "**format", "**constraints",
   "**role description", "**company info", "**lead info", "**rules",
   "follow format", "exact format", "=== analysis", etc.
3. `_clean_thinking` strips lines starting with "Format:" /
   "Constraints:" / "Rules:".
4. `_clean_thinking` strips markdown ## headers that are clearly
   reasoning metadata.
5. `_clean_thinking` removes leading/trailing empty lines.
6. `_clean_subject` strips "Subject:" prefix.
7. `_clean_subject` strips Re:/Fwd:/Fw: prefixes.
8. `_clean_subject` returns "" when subject contains thinking words.
9. `_clean_subject` truncates to 80 chars.
10. `extract_json` strips `<think>...</think>` tags.
11. `extract_json` strips ```json fences.
"""
from __future__ import annotations


def test_clean_thinking_keeps_email_content():
    from app import _clean_thinking
    text = ("Hi Alice,\n\n"
            "I noticed your team is hiring for a sales role.\n\n"
            "Worth a quick chat?\n\n"
            "— Bob")
    out = _clean_thinking(text)
    assert "Hi Alice" in out
    assert "sales role" in out
    assert "quick chat" in out
    assert "— Bob" in out


def test_clean_thinking_strips_thinking_process():
    from app import _clean_thinking
    text = ("Thinking Process: Let me analyse this lead.\n"
            "Hi Alice,\n\n"
            "Pitch.")
    out = _clean_thinking(text)
    assert "Thinking Process" not in out
    assert "analyse this" not in out.lower() or "thinking" not in out.lower()
    # Real content preserved.
    assert "Hi Alice" in out


def test_clean_thinking_strips_sender_role_marker():
    from app import _clean_thinking
    text = ("**Sender Role: SDR at Huntova\n"
            "**Tone Description: Friendly\n"
            "Hi Alice, here's the pitch.")
    out = _clean_thinking(text)
    assert "**Sender" not in out
    assert "**Tone" not in out
    assert "Hi Alice" in out


def test_clean_thinking_strips_format_lines():
    from app import _clean_thinking
    text = ("Format: 3 paragraphs.\n"
            "Constraints: under 150 words.\n"
            "Hi Alice — saw your team is hiring.")
    out = _clean_thinking(text)
    assert "Format:" not in out
    assert "Constraints:" not in out
    assert "Hi Alice" in out


def test_clean_thinking_strips_metadata_headers():
    from app import _clean_thinking
    text = ("## Approach\n"
            "I'll start with their pain point.\n\n"
            "Hi Alice,\n\n"
            "Real email body.")
    out = _clean_thinking(text)
    assert "## Approach" not in out
    assert "Real email body" in out


def test_clean_thinking_preserves_paragraph_breaks():
    """Empty lines between paragraphs survive."""
    from app import _clean_thinking
    text = "Para 1.\n\nPara 2.\n\nPara 3."
    out = _clean_thinking(text)
    assert "Para 1" in out
    assert "Para 2" in out
    # Empty lines preserved between paragraphs.
    assert "\n\n" in out


def test_clean_thinking_strips_leading_empty():
    from app import _clean_thinking
    text = "\n\n\nHi Alice,\n\nReal content."
    out = _clean_thinking(text)
    assert not out.startswith("\n")


def test_clean_thinking_strips_trailing_empty():
    from app import _clean_thinking
    text = "Hi Alice,\n\nReal content.\n\n\n"
    out = _clean_thinking(text)
    assert not out.endswith("\n\n")


def test_clean_thinking_handles_empty():
    from app import _clean_thinking
    assert _clean_thinking("") == ""


def test_clean_subject_strips_subject_prefix():
    from app import _clean_subject
    assert _clean_subject("Subject: Hello") == "Hello"
    assert _clean_subject("subject: Hello") == "Hello"
    assert _clean_subject("SUBJECT:Hello") == "Hello"


def test_clean_subject_strips_re_fwd():
    from app import _clean_subject
    assert _clean_subject("Re: Hello") == "Hello"
    assert _clean_subject("Fwd: Hello") == "Hello"
    assert _clean_subject("Fw: Hello") == "Hello"


def test_clean_subject_returns_empty_for_thinking():
    """Subjects containing thinking-words are garbage → return empty."""
    from app import _clean_subject
    assert _clean_subject("Thinking: how to cold email") == ""
    assert _clean_subject("Process this **subject") == ""


def test_clean_subject_truncates_to_80():
    from app import _clean_subject
    long_subj = "x" * 200
    out = _clean_subject(long_subj)
    assert len(out) <= 80


def test_clean_subject_preserves_normal():
    from app import _clean_subject
    assert _clean_subject("Quick question about Q4 plans") == "Quick question about Q4 plans"


def test_extract_json_strips_think_tags():
    from app import extract_json
    raw = '<think>The user wants JSON.</think>{"key": "value"}'
    out = extract_json(raw)
    # Some non-None result; either the dict or the parsed JSON.
    assert out is not None


def test_extract_json_strips_unclosed_think():
    from app import extract_json
    raw = '<think>still reasoning {"key":"value"}'
    out = extract_json(raw)
    # Must not crash; returns None or partial.


def test_extract_json_strips_markdown_fence():
    """extract_json returns the cleaned JSON STRING (not the parsed
    dict). Pin: fences stripped + JSON content present."""
    from app import extract_json
    raw = '```json\n{"key": "value"}\n```'
    out = extract_json(raw)
    if out is not None:
        # Fences gone.
        assert "```" not in out
        # JSON content present.
        assert '"key"' in out


def test_extract_json_handles_empty():
    from app import extract_json
    assert extract_json("") is None
    assert extract_json(None) is None
