"""Regression tests for BRAIN-143 (a525): the phase-5
question `prefill` field must preserve list shapes for
`multi_select` types instead of stringifying them. The
BRAIN-128 byte cap fix wrapped prefill in `str(...)`,
which destroys list-shaped prefills used to pre-select
multiple options.

Failure mode (Per Huntova engineering review on
type-aware AI-output validation):

The phase-5 question schema supports three types:
`text`, `single_select`, `multi_select`. For
`multi_select`, the AI can return a `prefill` list
of pre-selected option identifiers — the user opens
the question and the selected options are already
checked.

BRAIN-128 (a497) added per-field byte caps to phase-5
output. The prefill clip wrapped the raw value in
`str(q.get("prefill") or "").strip()` — which on a
list input produces `"['a', 'b']"` (Python repr), not
the original list. The frontend then can't pre-select
because it expects a list, not a string.

Per Huntova engineering review on type-aware
validation: the prefill normalizer must branch on
the question type. `multi_select` → preserve list
(each item byte-capped + count-capped). `text` /
`single_select` → string (current behavior).

Invariants:
- A new helper `_normalize_phase5_prefill(raw, q_type)`
  normalizes prefill type-aware.
- For `multi_select` + list input: returns list with
  byte-capped items + count cap.
- For text/single_select or non-list input: returns
  byte-capped string.
- Both `_normalize_phase5_questions` and the
  `api_wizard_generate_phase5` cleaner use the
  helper.
"""
from __future__ import annotations
import inspect


def test_normalize_phase5_prefill_helper_exists():
    """Module-scope helper exists."""
    import server as _s
    fn = getattr(_s, "_normalize_phase5_prefill", None)
    assert fn is not None and callable(fn), (
        "BRAIN-143 regression: server must expose "
        "`_normalize_phase5_prefill(raw, q_type)`."
    )


def test_multiselect_prefill_preserves_list():
    """Behavioral: multi_select + list prefill returns
    a list."""
    import server as _s
    out = _s._normalize_phase5_prefill(
        ["option_a", "option_b"], "multi_select"
    )
    assert isinstance(out, list)
    assert "option_a" in out
    assert "option_b" in out


def test_multiselect_prefill_clamps_oversize_items():
    """Behavioral: each item in the multi_select list
    is byte-capped to `_WIZARD_PHASE5_OPTION_BYTES_MAX`."""
    import server as _s
    cap = _s._WIZARD_PHASE5_OPTION_BYTES_MAX
    big_item = "x" * 100_000
    out = _s._normalize_phase5_prefill(
        [big_item, "small"], "multi_select"
    )
    assert isinstance(out, list)
    for item in out:
        assert isinstance(item, str)
        assert len(item.encode("utf-8")) <= cap


def test_multiselect_prefill_filters_non_strings():
    """Behavioral: non-string list items are filtered
    out (multi_select prefill should only contain
    string identifiers matching options)."""
    import server as _s
    out = _s._normalize_phase5_prefill(
        ["valid", 42, None, {"bad": True}, "also_valid"],
        "multi_select",
    )
    assert isinstance(out, list)
    # Only the string items survive.
    assert "valid" in out
    assert "also_valid" in out
    assert 42 not in out
    assert None not in out


def test_text_prefill_returns_string():
    """Behavioral: text type → byte-capped string,
    legacy behavior preserved."""
    import server as _s
    out = _s._normalize_phase5_prefill("hello world", "text")
    assert isinstance(out, str)
    assert out == "hello world"


def test_single_select_prefill_returns_string():
    """Behavioral: single_select picks one option →
    string."""
    import server as _s
    out = _s._normalize_phase5_prefill("option_a", "single_select")
    assert isinstance(out, str)
    assert out == "option_a"


def test_text_prefill_clamps_oversize_string():
    """Behavioral: text prefill is byte-capped to
    `_WIZARD_PHASE5_QUESTION_BYTES_MAX`."""
    import server as _s
    cap = _s._WIZARD_PHASE5_QUESTION_BYTES_MAX
    big = "x" * 100_000
    out = _s._normalize_phase5_prefill(big, "text")
    assert isinstance(out, str)
    assert len(out.encode("utf-8")) <= cap


def test_none_or_missing_prefill_returns_empty():
    """Behavioral: None / missing → empty string."""
    import server as _s
    assert _s._normalize_phase5_prefill(None, "text") == ""
    assert _s._normalize_phase5_prefill(None, "multi_select") in ([], "")


def test_normalize_phase5_questions_uses_prefill_helper():
    """Source-level: `_normalize_phase5_questions` uses
    the new prefill helper instead of inline str()."""
    import server as _s
    src = inspect.getsource(_s._normalize_phase5_questions)
    assert "_normalize_phase5_prefill(" in src, (
        "BRAIN-143 regression: `_normalize_phase5_questions` "
        "must use `_normalize_phase5_prefill` so multi_select "
        "list prefills are preserved instead of stringified."
    )


def test_generate_phase5_cleaner_uses_prefill_helper():
    """Source-level: the api_wizard_generate_phase5
    cleaner also uses the helper."""
    from server import api_wizard_generate_phase5
    src = inspect.getsource(api_wizard_generate_phase5)
    assert "_normalize_phase5_prefill(" in src, (
        "BRAIN-143 regression: api_wizard_generate_phase5 "
        "cleaner must use `_normalize_phase5_prefill` for "
        "the same parity. Pre-fix the cleaner stringified "
        "list prefills via `str(q.get('prefill') or '').strip()`."
    )
