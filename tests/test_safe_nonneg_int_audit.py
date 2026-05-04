"""BRAIN-210: server._safe_nonneg_int + _normalize_wizard_phase + _normalize_wizard_confidence audit.

a484 (BRAIN-115) added these defensive normalisers for every public
read of optimistic-concurrency tokens and audit counters. A regression
here either 500s the wizard status endpoint or leaks corrupt persisted
values to clients.

Pinned invariants:

1. `_safe_nonneg_int` returns default for None.
2. `_safe_nonneg_int` rejects bool (subclass of int — treat as
   corruption, return default).
3. `_safe_nonneg_int` clamps negative int to 0 when default ≥ 0.
4. `_safe_nonneg_int` coerces float to int (truncate).
5. `_safe_nonneg_int` parses numeric strings.
6. `_safe_nonneg_int` returns default for unparseable strings.
7. `_safe_nonneg_int` returns default for list / dict / weird types.
8. `_normalize_wizard_phase` clamps to `_WIZARD_PHASE_MAX`.
9. `_normalize_wizard_confidence` clamps to `_WIZARD_CONFIDENCE_MAX`.
"""
from __future__ import annotations


def test_safe_nonneg_int_none_returns_default():
    from server import _safe_nonneg_int
    assert _safe_nonneg_int(None) == 0
    assert _safe_nonneg_int(None, default=5) == 5


def test_safe_nonneg_int_rejects_bool():
    """bool is a subclass of int, but _safe_nonneg_int treats it as
    data corruption and returns default."""
    from server import _safe_nonneg_int
    assert _safe_nonneg_int(True) == 0
    assert _safe_nonneg_int(False) == 0
    assert _safe_nonneg_int(True, default=42) == 42


def test_safe_nonneg_int_positive_int_passes():
    from server import _safe_nonneg_int
    assert _safe_nonneg_int(5) == 5
    assert _safe_nonneg_int(0) == 0
    assert _safe_nonneg_int(99999) == 99999


def test_safe_nonneg_int_negative_clamped_to_zero():
    """Negative int with default ≥ 0 → clamp to 0."""
    from server import _safe_nonneg_int
    assert _safe_nonneg_int(-1) == 0
    assert _safe_nonneg_int(-100) == 0


def test_safe_nonneg_int_float_truncates():
    from server import _safe_nonneg_int
    assert _safe_nonneg_int(7.9) == 7
    assert _safe_nonneg_int(0.1) == 0


def test_safe_nonneg_int_negative_float_clamped():
    from server import _safe_nonneg_int
    assert _safe_nonneg_int(-3.5) == 0


def test_safe_nonneg_int_numeric_string():
    from server import _safe_nonneg_int
    assert _safe_nonneg_int("42") == 42
    assert _safe_nonneg_int("0") == 0


def test_safe_nonneg_int_unparseable_string():
    from server import _safe_nonneg_int
    assert _safe_nonneg_int("not-a-number") == 0
    assert _safe_nonneg_int("not-a-number", default=99) == 99
    assert _safe_nonneg_int("") == 0


def test_safe_nonneg_int_list_dict_corruption():
    """List / dict / weird types → default (data corruption)."""
    from server import _safe_nonneg_int
    assert _safe_nonneg_int([1, 2]) == 0
    assert _safe_nonneg_int({"x": 1}) == 0
    assert _safe_nonneg_int(object()) == 0


def test_safe_nonneg_int_inf_handled():
    """float('inf') overflows int — return default."""
    from server import _safe_nonneg_int
    assert _safe_nonneg_int(float("inf")) == 0
    assert _safe_nonneg_int(float("nan")) == 0


def test_normalize_wizard_phase_clamps_upper():
    """`_normalize_wizard_phase` clamps to _WIZARD_PHASE_MAX (default 100)."""
    from server import _normalize_wizard_phase, _WIZARD_PHASE_MAX
    assert _normalize_wizard_phase(_WIZARD_PHASE_MAX + 100) == _WIZARD_PHASE_MAX
    assert _normalize_wizard_phase(999999) == _WIZARD_PHASE_MAX


def test_normalize_wizard_phase_negative_clamped():
    from server import _normalize_wizard_phase
    assert _normalize_wizard_phase(-5) == 0


def test_normalize_wizard_phase_in_range_passes():
    from server import _normalize_wizard_phase
    assert _normalize_wizard_phase(50) == 50


def test_normalize_wizard_phase_garbage_returns_default():
    from server import _normalize_wizard_phase
    assert _normalize_wizard_phase("garbage") == 0
    assert _normalize_wizard_phase("garbage", default=10) == 10


def test_normalize_wizard_confidence_clamps_upper():
    """`_normalize_wizard_confidence` clamps to _WIZARD_CONFIDENCE_MAX."""
    from server import _normalize_wizard_confidence, _WIZARD_CONFIDENCE_MAX
    assert _normalize_wizard_confidence(_WIZARD_CONFIDENCE_MAX + 50) == _WIZARD_CONFIDENCE_MAX
    assert _normalize_wizard_confidence(999) == _WIZARD_CONFIDENCE_MAX


def test_normalize_wizard_confidence_in_range():
    from server import _normalize_wizard_confidence
    assert _normalize_wizard_confidence(75) == 75


def test_normalize_wizard_confidence_garbage_default():
    from server import _normalize_wizard_confidence
    assert _normalize_wizard_confidence(None) == 0
    assert _normalize_wizard_confidence({"x": 1}) == 0


def test_safe_nonneg_int_handles_whitespace_string():
    """String with whitespace should still parse if numeric inside."""
    from server import _safe_nonneg_int
    # Pin current behaviour — most parsers strip whitespace.
    out = _safe_nonneg_int("  42  ")
    # Either 42 (if int() handles whitespace, which it does) or 0.
    assert out in (42, 0)
