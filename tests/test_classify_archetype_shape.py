"""Regression test for BRAIN-8 (a369): _classify_archetype crashed
or silently produced garbage when wiz fields had unexpected shape:

- services as a string (legacy / buggy save) → iterated chars,
  matched single-char keyword fragments
- services as a list containing None / dict / int → AttributeError
  on `.lower()`
- business_description not a string → crash on `.lower()` / `+`

Same bug class as BRAIN-7 (a368) but in archetype classification.
"""
from __future__ import annotations


def test_string_services_does_not_crash():
    from app import _classify_archetype
    result = _classify_archetype({"services": "consulting, strategy"})
    assert isinstance(result, dict)
    assert "primary" in result and "secondary" in result and "confidence" in result


def test_list_with_non_string_items_does_not_crash():
    from app import _classify_archetype
    result = _classify_archetype({"services": ["consulting", None, 42, {"name": "x"}, "advisory"]})
    assert isinstance(result, dict)
    # The two valid strings should still trigger consultant matching.
    assert result["primary"] in ("consultant", "other")


def test_non_string_business_description_does_not_crash():
    from app import _classify_archetype
    result = _classify_archetype({"business_description": ["fragment a", "fragment b"]})
    assert isinstance(result, dict)
    assert "primary" in result


def test_none_wiz_fields_default_safely():
    from app import _classify_archetype
    result = _classify_archetype({"services": None, "business_description": None})
    assert isinstance(result, dict)
    assert result["primary"] == "other"


def test_empty_wiz_returns_other():
    from app import _classify_archetype
    result = _classify_archetype({})
    assert result["primary"] == "other"
    assert result["confidence"] == 20
