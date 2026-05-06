"""Regression test for BRAIN-24 (a385): _dna_build_stage_1_prompt
crashed on non-string list items via `", ".join(...)`, and on
`None`-valued `_site_context` via `[:1500]` slicing.

Same shape-mismatch class as BRAIN-7/8/9/21/23 — the DNA Stage 1
prompt builder reads many wizard fields and assumes they're all
clean strings/lists-of-strings. Real wizard data isn't always.
"""
from __future__ import annotations


def test_stage1_does_not_crash_on_list_with_non_strings():
    from app import _dna_build_stage_1_prompt
    wd = {
        "company_name": "Acme",
        "regions": ["UK", None, "US", 42],  # mixed shapes
        "services": ["consulting", None],
        "buyer_roles": [{"name": "x"}, "CEO"],
    }
    out = _dna_build_stage_1_prompt(wd)
    assert isinstance(out, str)
    assert "Acme" in out


def test_stage1_does_not_crash_on_none_site_context():
    from app import _dna_build_stage_1_prompt
    wd = {
        "company_name": "Acme",
        "_site_context": None,  # explicit None, key present
    }
    out = _dna_build_stage_1_prompt(wd)
    assert isinstance(out, str)


def test_stage1_does_not_crash_on_string_shaped_lists():
    """If services / regions arrived as a comma-separated string
    (legacy save), the prompt builder must not iterate it as chars."""
    from app import _dna_build_stage_1_prompt
    wd = {
        "company_name": "Acme",
        "services": "consulting, advisory",  # string, not list
        "regions": "UK, US",  # string, not list
    }
    out = _dna_build_stage_1_prompt(wd)
    assert isinstance(out, str)
