"""Regression test for BRAIN-17 (a378): _wiz_rules was being
re-loaded via load_settings() per-lead inside the scoring loop,
causing N redundant DB reads per hunt AND mid-hunt rule-change
inconsistency (a /api/settings PATCH between leads would cause
some leads in the same hunt to use new rules, others old).

Fix: reuse the _wiz_data already loaded at hunt start. Same blob,
no DB hit, consistent across all leads in the hunt.
"""
from __future__ import annotations
import inspect


def test_wiz_rules_does_not_reload_per_lead():
    """The lead-scoring loop must NOT call load_settings() to get
    _wiz_rules. It should use the snapshot loaded at hunt start
    (_wiz_data) so all leads in the hunt see the same rules."""
    import app
    src = inspect.getsource(app)
    # The lead-scoring region. Marker: hard-reject safety gates comment.
    region_start = src.find("# ── Hard-reject safety gates — profile-driven ──")
    region_end = src.find("if fit >= _FIT_MIN", region_start) if region_start != -1 else -1
    assert region_start != -1 and region_end != -1, (
        "BRAIN-17: hard-reject region markers not found — test stale?"
    )
    region = src[region_start:region_end]
    assert "load_settings()" not in region, (
        "BRAIN-17 regression: lead-scoring loop must not call "
        "load_settings() per-lead — N DB reads per hunt AND a /api/"
        "settings PATCH mid-hunt would split the hunt into two rule "
        "regimes. Use the cached `_wiz_data` snapshot from hunt start."
    )
