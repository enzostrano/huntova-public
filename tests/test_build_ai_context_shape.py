"""Regression test for BRAIN-35 (a396): _build_ai_context runs
per-lead inside analyse_lead. Pre-fix any non-string / non-list
field crashed `', '.join(...)` mid-prompt-build, silently failing
the whole lead analysis.
"""
from __future__ import annotations
from unittest import mock


def test_build_ai_context_handles_shape_mismatches(local_env):
    """Inject malformed wizard data and verify no crash."""
    bad_settings = {
        "wizard": {
            "company_name": None,
            "business_description": ["frag1", "frag2"],
            "services": "consulting, advisory",  # string-shape
            "differentiators": [None, "fast", 42],  # mixed
            "_knowledge": "not a list",
            "confirmed_facts": None,
        }
    }
    with mock.patch("app.load_settings", return_value=bad_settings):
        from app import _build_ai_context
        out = _build_ai_context()
        assert isinstance(out, str)
        # Should reference the placeholder when company_name is None.
        assert "our company" in out


def test_build_ai_context_clean_input_works():
    """Don't regress the happy path."""
    good_settings = {
        "wizard": {
            "company_name": "Acme",
            "business_description": "We help SMBs scale",
            "services": ["consulting", "advisory"],
        }
    }
    with mock.patch("app.load_settings", return_value=good_settings):
        from app import _build_ai_context
        out = _build_ai_context()
        assert "Acme" in out
        assert "consulting" in out
