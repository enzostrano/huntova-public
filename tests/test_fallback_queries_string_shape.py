"""Regression test for BRAIN-12 (a373): _fallback_queries (the
last-resort query generator when DNA + brain-template paths fail)
silently degraded to empty term lists when wizard fields arrived
as strings instead of lists.

Same shape-mismatch class as BRAIN-7 (a368) and BRAIN-8 (a369),
but in the structured fallback path. No crash — the list-comp
iterates string as chars, filters length-1 chars, returns []. The
deeper-fallback at app.py:5651 catches it via target_clients
parsing so the agent doesn't crash, but it produces generic
"company business" / "services solutions" queries that find
garbage leads.

Per GPT-5.4's senior-engineer audit (Perplexity, this session)
priority #5: "silent fallback degradation."
"""
from __future__ import annotations


def test_fallback_queries_string_services_produces_terms():
    """If services arrives as a string (legacy save / older client),
    the function must split it into individual terms and use them,
    NOT iterate the string as chars and silently lose them."""
    from app import _fallback_queries
    queries = _fallback_queries({
        "services": "consulting, growth strategy, advisory",
        "icp_industries": ["fintech"],
        "buyer_roles": ["CEO"],
    }, ["UK"])
    assert isinstance(queries, list)
    # The 3 services should each appear in at least one query.
    joined = " | ".join(queries).lower()
    assert "consulting" in joined or "advisory" in joined or "growth" in joined, (
        f"BRAIN-12 regression: services as string lost — got queries: {queries[:5]}"
    )


def test_fallback_queries_string_industries_produces_terms():
    from app import _fallback_queries
    queries = _fallback_queries({
        "services": ["consulting"],
        "icp_industries": "fintech, healthtech, retailtech",
        "buyer_roles": ["CEO"],
    }, ["UK"])
    assert isinstance(queries, list)
    joined = " | ".join(queries).lower()
    assert "fintech" in joined or "healthtech" in joined or "retailtech" in joined, (
        f"BRAIN-12 regression: industries as string lost — got queries: {queries[:5]}"
    )


def test_fallback_queries_list_with_non_string_items_does_not_crash():
    """Same defensive class as BRAIN-8 — a list containing None / dict
    / int alongside strings shouldn't crash the function."""
    from app import _fallback_queries
    queries = _fallback_queries({
        "services": ["consulting", None, 42, {"name": "x"}, "advisory"],
        "icp_industries": ["fintech"],
        "buyer_roles": ["CEO"],
    }, ["UK"])
    assert isinstance(queries, list)
    joined = " | ".join(queries).lower()
    assert "consulting" in joined or "advisory" in joined


def test_fallback_queries_existing_list_path_unchanged():
    """Don't regress the canonical list-of-strings happy path."""
    from app import _fallback_queries
    queries = _fallback_queries({
        "services": ["consulting", "advisory"],
        "icp_industries": ["fintech"],
        "buyer_roles": ["CEO"],
    }, ["UK"])
    assert isinstance(queries, list) and queries
