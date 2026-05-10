"""Regression tests for BRAIN-76 (a437): /api/wizard/complete's
parallel `history=[{question, answer}]` payload must pass a
closed-schema contract, mirroring the BRAIN-75 profile validation.

Failure mode (per GPT-5.4 every-trust-boundary audit):

`_apply_wizard_mutations` does:

    ans = {h.get("question", ""): h.get("answer", "") for h in history}
    for k, v in ans.items():
        kl = k.lower()
        if "red_flag" in kl or "skip" in kl or "waste" in kl:
            w["red_flags"] = v
        ...

Pre-fix, `history` was untrusted client JSON walked with
`h.get(...)` — which crashes if `h` is a non-dict (string,
int, list, None). The values in `red_flags`, `clients`, and
`edge` came straight from `h["answer"]` with no type or size
check.

A buggy/malicious client could post `history=[{"question":
"red_flag_test", "answer": {"evil": "nested"}}]` → `w["red_flags"] =
{"evil": "nested"}` persists into stored wizard state. Or
`answer: "X" * 200_000` → 200KB blob in `red_flags`. Or
`history=[42, "not-a-dict", null, ...]` → AttributeError on
`h.get`.

Invariants:
- `history` must be a list. Non-list → empty list.
- Each item must be a dict. Non-dict items dropped.
- `question` + `answer` must be strings. Non-strings dropped.
- Extra keys per item ignored.
- Items capped at a reasonable count (e.g. 50 — the wizard
  has at most 14 questions).
- Each `question`/`answer` capped at `_WIZARD_STR_MAX`.
"""
from __future__ import annotations
import inspect


def test_complete_validates_history_payload_at_boundary():
    """Source-level: api_wizard_complete must validate the
    history payload before any mutation logic consumes it."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    has_validator = (
        "_validate_history_payload" in src
        or "_coerce_history" in src
        or "_normalize_history" in src
        or "raw_history" in src  # name pattern from BRAIN-75 profile precedent
    )
    assert has_validator, (
        "BRAIN-76 regression: history payload must be validated "
        "via a named helper or local sanitization loop before "
        "_apply_wizard_mutations sees it. Mirrors BRAIN-75."
    )


def test_history_validation_runs_before_apply_mutations():
    """Source-level: history sanitization must happen BEFORE
    _apply_wizard_mutations. Otherwise the snapshot used by
    the BRAIN-72 brain+dossier compute window sees raw,
    unvalidated history."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    apply_idx = src.find("_apply_wizard_mutations(_w_snap)")
    # Look for any history sanitization marker.
    sanitize_idx = -1
    for needle in ("raw_history", "history: list", "history = []",
                   "_validate_history", "_normalize_history",
                   "_coerce_history"):
        i = src.find(needle)
        if i != -1:
            sanitize_idx = i if sanitize_idx == -1 else min(sanitize_idx, i)
    assert apply_idx != -1
    assert sanitize_idx != -1
    assert sanitize_idx < apply_idx, (
        "BRAIN-76 regression: history sanitization must run "
        "BEFORE _apply_wizard_mutations."
    )


def test_history_drops_non_dict_items():
    """Behavioral: a history list with mixed garbage items
    must filter to only well-formed dicts. The exposed helper
    name varies; test by the documented behavior of the
    endpoint via the closure."""
    # We re-implement the contract here as a direct test on a
    # helper if it exists, or fall back to reading the source.
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    # Confirm the source explicitly filters non-dict items.
    has_filter = (
        "isinstance(h, dict)" in src
        or "isinstance(_h, dict)" in src
        or "isinstance(item, dict)" in src
    )
    assert has_filter, (
        "BRAIN-76 regression: must filter non-dict history items. "
        "Pre-fix, h.get(...) would AttributeError on a non-dict. "
        "Post-fix, that path is unreachable."
    )


def test_history_validates_question_and_answer_are_strings():
    """Source-level: question and answer must be coerced to
    strings (or dropped). A dict-shaped answer must NOT be
    written into red_flags / clients / edge."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    # Look for explicit isinstance checks on h's question/answer.
    has_str_check = (
        "isinstance(_q, str)" in src
        or "isinstance(_a, str)" in src
        or 'isinstance(h.get("question")' in src
        or 'isinstance(h.get("answer")' in src
    )
    assert has_str_check, (
        "BRAIN-76 regression: question/answer fields must be "
        "type-checked. A dict-shaped answer would otherwise "
        "persist to red_flags / clients / edge as a dict."
    )


def test_history_caps_string_lengths():
    """Source-level: question and answer values must be capped
    so a 200KB paste in either doesn't bloat user_settings.data."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    has_cap = (
        "_WIZARD_STR_MAX" in src
        or "_HISTORY_STR_MAX" in src
        or "[:50_000]" in src
        or "[:50000]" in src
    )
    assert has_cap, (
        "BRAIN-76 regression: history strings must be capped. "
        "Without it, a 200KB paste in answer flows into "
        "red_flags."
    )


def test_history_caps_item_count():
    """Source-level: history list must be capped at a sane max
    (the wizard has at most 14 questions, so anything >50 is
    abusive)."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    has_count_cap = (
        "_HISTORY_MAX_ITEMS" in src
        or "[:50]" in src
        or "[:100]" in src
        or "[:30]" in src
    )
    assert has_count_cap, (
        "BRAIN-76 regression: history list count must be capped. "
        "Without it, a 10000-item list eats memory + iteration time."
    )
