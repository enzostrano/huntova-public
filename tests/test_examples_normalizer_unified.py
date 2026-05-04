"""Regression test for BRAIN-9 (a370): consolidate the three copies
of the "examples normalize" logic into a single module-level
canonical helper, and have _build_hunt_brain normalize at write
time so downstream consumers see exactly one shape.

GPT-5.4 (via Perplexity) explicitly called this out as the next
high-leverage move: "the next bug is 'function A normalized this
field, function B assumed the pre-normalized contract.'" The bug
class is bug-prevention, not a single crash, but it eliminates the
shape-drift family of bugs that produced BRAIN-7 (a368).
"""
from __future__ import annotations


def test_module_level_normalize_examples_exists():
    """The canonical normalizer must be importable directly from app —
    not buried inside another function. Single source of truth."""
    from app import _normalize_examples
    assert callable(_normalize_examples)


def test_normalize_examples_handles_all_shapes():
    from app import _normalize_examples
    # String shape (textarea natural shape)
    assert _normalize_examples("Acme — long retainer\nBeta Co — niche") == [
        {"name": "Acme — long retainer", "reason": ""},
        {"name": "Beta Co — niche", "reason": ""},
    ]
    # List-of-strings
    assert _normalize_examples(["Acme", "Beta"]) == [
        {"name": "Acme", "reason": ""},
        {"name": "Beta", "reason": ""},
    ]
    # List-of-dicts (pre-normalized)
    assert _normalize_examples([{"name": "Acme", "reason": "fast"}]) == [
        {"name": "Acme", "reason": "fast"},
    ]
    # None / empty
    assert _normalize_examples(None) == []
    assert _normalize_examples("") == []
    assert _normalize_examples([]) == []
    # Garbage
    assert _normalize_examples(42) == []


def test_build_hunt_brain_stores_normalized_examples():
    """The brain must store examples in the canonical list-of-dicts
    shape so downstream consumers don't each have to renormalize.
    Was the source of BRAIN-7 — _generate_brain_queries assumed
    pre-normalized but got raw."""
    from app import _build_hunt_brain
    brain = _build_hunt_brain({
        "services": ["consulting"],
        "example_good_clients": "Acme — fast\nBeta — niche",
        "example_bad_clients": "BadCorp",
        "business_description": "we help SMBs",
    })
    egc = brain["example_good_clients"]
    ebc = brain["example_bad_clients"]
    assert isinstance(egc, list), "must be a list, not the raw string"
    assert all(isinstance(item, dict) and "name" in item for item in egc), (
        "each entry must be a dict with 'name' key"
    )
    assert egc[0]["name"] == "Acme — fast"
    assert egc[1]["name"] == "Beta — niche"
    assert isinstance(ebc, list)
    assert ebc[0]["name"] == "BadCorp"


def test_generate_brain_queries_no_longer_renormalizes():
    """Sanity: the brain stores the canonical shape, so passing a
    pre-built brain to _generate_brain_queries works without any
    re-normalization step. The function should still handle raw
    inputs defensively (a368 fix), but the happy path is now
    'brain stores canonical → consumer reads canonical'."""
    from app import _build_hunt_brain, _generate_brain_queries
    brain = _build_hunt_brain({
        "services": ["consulting"],
        "buyer_roles": ["CEO"],
        "icp_industries": ["fintech"],
        "example_good_clients": "Acme — fast",
    })
    queries = _generate_brain_queries(brain, [])
    assert any("Acme" in q for q in queries)
