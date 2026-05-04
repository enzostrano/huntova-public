"""Regression test for BRAIN-32 (a393): score comparisons used
`lead.get("fit_score", 0)` then `score < 5` — if value is None,
the comparison crashes with TypeError. AI scoring output
occasionally returns null fields (provider quirk, malformed
JSON) → `None < 5` blows up → the lead silently fails its
verification check.
"""
from __future__ import annotations


def test_validate_score_handles_none_fit_score():
    """validate_score must short-circuit on a None fit_score
    without raising TypeError."""
    from app import validate_score
    out = validate_score({"fit_score": None}, "")
    # Pre-fix: TypeError on `None < 5`. Post-fix: returns 0 or score.
    assert isinstance(out, (int, float))


def test_calculate_priority_score_handles_none_fit():
    """calculate_priority_score reads `fit = lead.get("fit_score", 0)`
    and multiplies. None * 10 crashes."""
    from app import calculate_priority_score
    out = calculate_priority_score({"fit_score": None})
    assert isinstance(out, (int, float))
