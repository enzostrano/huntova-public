"""Regression test for BRAIN-7 (a368): _generate_brain_queries
crashed with `AttributeError: 'str' object has no attribute 'get'`
when `example_good_clients` was the natural string-from-textarea
shape (the wizard question is a textarea so the user's answer is
always a string before it reaches the brain).

Same bug class as a331's _generate_training_dossier crash —
_normalize_examples_top fixed THAT function but the same shape
mismatch in _generate_brain_queries was missed.
"""
from __future__ import annotations


def _make_brain(example_good_clients):
    """Minimal brain blob — enough fields for the function to run
    through to the example-client-derived queries section."""
    return {
        "hunt_brain_version": 1,
        "archetype": "consultant",
        "services_clean": ["growth strategy"],
        "preferred_industries": ["fintech"],
        "buyer_roles_clean": ["CEO"],
        "triggers_clean": ["new round"],
        "ideal_company_size": ["50-200"],
        "example_good_clients": example_good_clients,
    }


def test_string_example_good_clients_does_not_crash():
    """The wizard's textarea stores this as a string. Hunt query
    generation must handle that shape without raising."""
    from app import _generate_brain_queries
    brain = _make_brain("Acme — long-term retainer\nBeta Co — niche specialist")
    # Pre-fix: AttributeError on `'A'.get('name', '')`
    queries = _generate_brain_queries(brain, [])
    assert isinstance(queries, list), "must return a list, not raise"
    # The fix should turn the string into entries, so we expect AT
    # LEAST one query referencing the first named example.
    assert any("Acme" in q for q in queries), (
        "BRAIN-7 regression: string-shaped example_good_clients "
        "should still produce 'companies like Acme' style queries "
        "after the normalize step."
    )


def test_list_of_dicts_still_works():
    """Don't regress the dict-of-name shape that the dossier path
    uses internally."""
    from app import _generate_brain_queries
    brain = _make_brain([{"name": "Acme", "reason": "great fit"}])
    queries = _generate_brain_queries(brain, [])
    assert isinstance(queries, list)
    assert any("Acme" in q for q in queries)


def test_list_of_strings_works():
    """Some persisted blobs are list-of-strings (CSV-style import,
    older wizard versions). Must also be handled."""
    from app import _generate_brain_queries
    brain = _make_brain(["Acme", "Beta Co"])
    queries = _generate_brain_queries(brain, [])
    assert isinstance(queries, list)
    assert any("Acme" in q for q in queries)


def test_empty_example_good_clients_no_crash():
    """Empty / None should produce zero example-derived queries
    but not crash."""
    from app import _generate_brain_queries
    brain = _make_brain("")
    queries = _generate_brain_queries(brain, [])
    assert isinstance(queries, list)
    brain2 = _make_brain(None)
    queries2 = _generate_brain_queries(brain2, [])
    assert isinstance(queries2, list)
