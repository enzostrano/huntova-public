"""Regression test for BRAIN-20 (a381): two more per-lead
load_settings() calls in the agent loop:
- app.py:8275 — `_ms = load_settings().get("max_pages_per_lead")`
- app.py:8799 — `_us = load_settings() or {}` (auto-tag/min-fit/stage block)

Same class as BRAIN-17/19 — these were missed in those passes.
This test enforces the invariant that the agent's per-lead loop
reads from the hunt-start cached snapshot, not via repeated
load_settings() calls.
"""
from __future__ import annotations
import inspect


def test_max_pages_per_lead_uses_cached_snapshot():
    """Source-level: the max_pages_per_lead lookup must NOT call
    load_settings() per-lead."""
    import app
    src = inspect.getsource(app)
    # Locate the lookup
    region_start = src.find("max_pages_per_lead")
    assert region_start != -1
    # 200 chars before should NOT contain a fresh load_settings()
    region = src[max(0, region_start - 200):region_start + 100]
    assert "load_settings().get(\"max_pages_per_lead\"" not in region, (
        "BRAIN-20 regression: max_pages_per_lead lookup must use the "
        "hunt-start cached settings snapshot, not load_settings() per-lead."
    )


def test_auto_tag_block_uses_cached_snapshot():
    """Source-level: the auto-tag / min-fit / default-stage block
    (a248 era) must NOT call load_settings() per-lead."""
    import app
    src = inspect.getsource(app)
    region_start = src.find("# Auto-tag keywords (CRM tab)")
    assert region_start != -1, "auto-tag marker not found — test stale?"
    region = src[max(0, region_start - 400):region_start + 200]
    assert "_us = load_settings()" not in region, (
        "BRAIN-20 regression: auto-tag/min-fit/stage block must use "
        "the hunt-start cached settings snapshot."
    )
