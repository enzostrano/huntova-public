"""Regression test for BRAIN-23 (a384): generate_agent_dna's
post-processing read company_name and services without shape
coercion. Same class as BRAIN-7/8/9/21.

These tests don't actually call the AI (the function early-returns
through _dna_fallback when AI fails). We just verify that the
post-processing branch doesn't crash on the obvious shape
mismatches that real production data carries.
"""
from __future__ import annotations
import inspect


def test_dna_post_processing_coerces_company_name():
    """Source-level: company_name lookup must defend against None /
    non-string values."""
    import app
    src = inspect.getsource(app.generate_agent_dna)
    # Look for the company_name extraction
    idx = src.find('company_name')
    assert idx != -1
    # The fix should use isinstance OR `or ""` chain pattern, not
    # raw .get(key, default) which lets None through.
    region = src[idx:idx + 200]
    bad_pattern = 'wizard_data.get("company_name", "").lower()'
    assert bad_pattern not in region, (
        "BRAIN-23 regression: company_name extraction must defend "
        "against None / non-string values. `.get(key, default)` "
        "lets value=None pass through and crash on .lower()."
    )


def test_dna_post_processing_coerces_services():
    """Source-level: services lookup must defend against string-shape
    (legacy save) which would iterate chars in the for-loop."""
    import app
    src = inspect.getsource(app.generate_agent_dna)
    # The post-processing does `services = wizard_data.get("services", [])`
    # then iterates. Need explicit isinstance check OR coercion helper.
    idx = src.find('services = wizard_data.get("services"')
    if idx == -1:
        # The fix may have rephrased — looser check
        idx = src.find('services = ')
    assert idx != -1
    region = src[idx:idx + 300]
    # Look for either an explicit isinstance check OR a coercion
    # helper call within 300 chars after the assignment.
    has_defense = ("isinstance(services" in region
                   or "_to_str_list(" in region
                   or "isinstance(_raw_services" in region
                   or "if isinstance(s, str)" in region)
    assert has_defense, (
        "BRAIN-23 regression: services must be defensively coerced "
        "to a list-of-strings before iteration in the post-processing "
        "branch. String-shape services (legacy save) would iterate "
        "as characters and silently produce garbage service_words."
    )
