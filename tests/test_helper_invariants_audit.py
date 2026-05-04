"""Regression tests for BRAIN-157 (a570): blanket
invariant audit on the BRAIN-* helper family. Every
helper that takes raw input must defensively handle
None/non-string types without raising. A single
unhandled TypeError on a hot-path helper takes down
the request.

Failure mode: a future refactor narrows a helper's
input handling (e.g. drops the `if isinstance(raw, str)`
branch), and a corrupted persisted value crashes the
status endpoint or save flow.

Invariants tested as a blanket sweep:
- `_safe_nonneg_int(None)` returns 0.
- `_normalize_dna_state(None)` returns "unset".
- `_clip_to_byte_budget(None, 100)` returns "" (not raises).
- `_idempotency_key_clean(None)` returns None.
- `_normalize_phase5_prefill(None, "text")` returns "".
- `_normalize_wizard_phase(None)` returns 0.
- `_normalize_wizard_confidence(None)` returns 0 (if exists).
- `_dna_pending_is_stale(None)` returns True (fail-open).
"""
from __future__ import annotations


def test_safe_nonneg_int_handles_none():
    import server as _s
    assert _s._safe_nonneg_int(None) == 0
    assert _s._safe_nonneg_int(None, default=42) == 42


def test_normalize_dna_state_handles_none():
    import server as _s
    assert _s._normalize_dna_state(None) == "unset"


def test_clip_to_byte_budget_handles_none():
    import server as _s
    out = _s._clip_to_byte_budget(None, 100)
    assert out in (None, "")


def test_idempotency_key_clean_handles_none():
    import server as _s
    assert _s._idempotency_key_clean(None) is None
    assert _s._idempotency_key_clean("") is None


def test_normalize_phase5_prefill_handles_none():
    import server as _s
    out_text = _s._normalize_phase5_prefill(None, "text")
    assert out_text == ""
    out_multi = _s._normalize_phase5_prefill(None, "multi_select")
    assert out_multi == [] or out_multi == ""


def test_normalize_wizard_phase_handles_none():
    import server as _s
    assert _s._normalize_wizard_phase(None) == 0


def test_dna_pending_is_stale_fail_opens():
    """Defensive: missing/None timestamp returns True
    so corrupted leases self-recover."""
    import server as _s
    assert _s._dna_pending_is_stale(None) is True
    assert _s._dna_pending_is_stale("") is True


def test_safe_nonneg_int_rejects_non_numeric_strings():
    """Strings that don't parse as ints fall back to default."""
    import server as _s
    assert _s._safe_nonneg_int("banana") == 0
    assert _s._safe_nonneg_int("abc123") == 0


def test_clip_to_byte_budget_handles_non_string():
    """Non-string types coerce via str() not raise."""
    import server as _s
    out = _s._clip_to_byte_budget(42, 100)
    assert isinstance(out, str)
    out_dict = _s._clip_to_byte_budget({"x": 1}, 100)
    assert isinstance(out_dict, str)


def test_normalize_phase5_questions_handles_non_list():
    """Non-list input returns []."""
    import server as _s
    assert _s._normalize_phase5_questions(None) == []
    assert _s._normalize_phase5_questions("not a list") == []
    assert _s._normalize_phase5_questions(42) == []
    assert _s._normalize_phase5_questions({"x": 1}) == []
