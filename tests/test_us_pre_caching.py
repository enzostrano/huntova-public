"""Regression test for BRAIN-19 (a380): another per-lead
load_settings() call at app.py:8742 (filter-keywords block) was
missed in BRAIN-17 (which hoisted the wizard rules). Same class:
N redundant DB reads per hunt, mid-hunt settings PATCH would
split hunt into two regimes for reject_keywords / must_have_keywords
/ language_filter.
"""
from __future__ import annotations
import inspect


def test_filter_keywords_block_does_not_reload_per_lead():
    """The reject_keywords / must_have_keywords / language_filter
    block must NOT call load_settings() per-lead. Should use the
    cached snapshot from hunt start."""
    import app
    src = inspect.getsource(app)
    region_start = src.find("# Reject keywords — drop on any match")
    assert region_start != -1, "filter-keywords marker not found — test stale?"
    # Look at the 200 lines BEFORE the marker — that's where load_settings()
    # would be called inside the loop.
    region = src[max(0, region_start - 800):region_start + 1000]
    # Find any per-lead `_us_pre = load_settings()` style call.
    has_per_lead_load = "load_settings()" in region and "_us_pre" in region
    # The fix should reuse a hunt-start snapshot. We accept any of:
    # - explicit cache name
    # - removal of per-lead load_settings
    # The bug specifically is "load_settings() inside the per-lead loop".
    # Detect: if `_us_pre =` appears in the region without `# a380` (the
    # fix marker) nearby, it's the old code.
    if has_per_lead_load:
        # Acceptable iff the assignment is `_us_pre = _hunt_settings_snapshot`
        # (or similar cached-name pattern), NOT `_us_pre = load_settings()`.
        bad_pattern = "_us_pre = load_settings()"
        assert bad_pattern not in region, (
            "BRAIN-19 regression: filter-keywords block must not call "
            "load_settings() per-lead — same class as BRAIN-17. Use "
            "the hunt-start cached settings snapshot."
        )
