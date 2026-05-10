"""Regression test for BRAIN-26 (a387): _dna_fallback had the same
shape bugs as Stage 1/2 (BRAIN-24/25). The fallback runs WHEN
Stage 1 already failed — if it ALSO crashes on shape-mismatched
fields, the user gets no DNA at all.
"""
from __future__ import annotations


def test_dna_fallback_handles_shape_mismatches():
    from app import _dna_fallback
    wd = {
        "company_name": None,
        "business_description": ["frag1", "frag2"],
        "services": "consulting, advisory",
        "target_clients": 42,
        "outreach_tone": None,
    }
    out = _dna_fallback(wd, version=1)
    assert isinstance(out, dict)
    assert "business_context" in out
    # No crash, sensible defaults
    assert "the company" in out["business_context"]


def test_dna_fallback_clean_input_unchanged():
    from app import _dna_fallback
    wd = {
        "company_name": "Acme",
        "business_description": "We help SMBs scale",
        "services": ["consulting", "advisory"],
        "target_clients": "Series A founders",
    }
    out = _dna_fallback(wd, version=1)
    assert "Acme" in out["business_context"]
    assert "consulting" in out["business_context"]
