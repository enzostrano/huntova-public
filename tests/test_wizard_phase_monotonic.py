"""Regression test for BRAIN-3 (a364): /api/wizard/save-progress was
unconditionally setting `_wizard_phase = phase`, so a stale request
from one tab could regress the persisted phase below what another
tab had already saved. The fix introduces `_monotonic_phase(prev,
incoming)` in server.py — verifies it only goes forward + handles
the malformed-JSON edge cases that the mutator could see in practice
(legacy clients, schema drift, None from an empty save-progress body).
"""
from __future__ import annotations


def test_monotonic_phase_picks_max():
    from server import _monotonic_phase
    assert _monotonic_phase(2, 4) == 4
    assert _monotonic_phase(4, 2) == 4, "stale phase=2 must not regress saved phase=4"
    assert _monotonic_phase(0, 1) == 1
    assert _monotonic_phase(1, 0) == 1


def test_monotonic_phase_treats_none_as_zero():
    from server import _monotonic_phase
    assert _monotonic_phase(None, 3) == 3
    assert _monotonic_phase(3, None) == 3
    assert _monotonic_phase(None, None) == 0


def test_monotonic_phase_handles_malformed_input():
    """Legacy clients and JSON drift can send strings or floats. The
    mutator must coerce safely instead of raising mid-transaction."""
    from server import _monotonic_phase
    assert _monotonic_phase("3", 1) == 3
    assert _monotonic_phase(1, "5") == 5
    assert _monotonic_phase("not-a-number", 2) == 2, "garbage prev → fall back to incoming"
    assert _monotonic_phase(2, "not-a-number") == 2, "garbage incoming → keep prev"
    assert _monotonic_phase("garbage", "garbage") == 0
