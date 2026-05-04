"""Regression test for BRAIN-25 (a386): _dna_build_stage_2_prompt
had the same shape-mismatch bugs as Stage 1 (BRAIN-24/a385) on
regions + company_name. Mirror fix.
"""
from __future__ import annotations


def test_stage2_does_not_crash_on_list_with_non_strings():
    from app import _dna_build_stage_2_prompt
    wd = {"company_name": "Acme", "regions": ["UK", None, 42, "US"]}
    strategy = {"hunting_channels": []}
    out = _dna_build_stage_2_prompt(wd, strategy)
    assert isinstance(out, str)
    assert "Acme" in out


def test_stage2_handles_none_company_name():
    from app import _dna_build_stage_2_prompt
    wd = {"company_name": None}
    strategy = {"hunting_channels": []}
    out = _dna_build_stage_2_prompt(wd, strategy)
    assert isinstance(out, str)
    # Should fall through to "the company" placeholder, not interpolate "None".
    assert "None" not in out.split("\n")[0]


def test_stage2_handles_string_shape_regions():
    from app import _dna_build_stage_2_prompt
    wd = {"company_name": "Acme", "regions": "UK, US, France"}
    strategy = {"hunting_channels": []}
    out = _dna_build_stage_2_prompt(wd, strategy)
    assert isinstance(out, str)
    # The string-shape should be split into proper region tokens, not
    # iterated as chars.
    assert "UK" in out and "France" in out
