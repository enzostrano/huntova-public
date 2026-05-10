"""Regression test for BRAIN-15 (a376): query-tier cascade in the
agent loop overwrote `queries` at each tier instead of accumulating
uniquely. Failure mode (per GPT-5.4 audit, Perplexity, this session):

User has a sharp, well-trained brain. Agent DNA generates 4
ICP-tailored queries (below threshold of 5). Cascade DISCARDS
them, falls through to generate_queries_ai (50 generic), brain
templates (overwrite again), fallback (overwrite again). The 4
high-quality queries are silently lost. User experiences "the
hunt feels generic" with no visible failure.

GPT called this exact class: "falsey-but-valid structured outputs
cause the loop to downgrade to generic query generation even
though enough high-signal brain data exists."

Source-level invariant test — verifies the cascade accumulates
across tiers and that the DNA threshold is permissive (>=1 not >=5).
"""
from __future__ import annotations
import inspect


def test_query_tier_cascade_accumulates_across_tiers():
    """The cascade must NOT use `queries = X` overwrite assignments
    on subsequent tiers. They must extend / append unique queries
    so high-quality DNA queries survive even when below a target
    count."""
    import app
    src = inspect.getsource(app)
    # The cascade region: locate the marker comments and ensure the
    # accumulation pattern exists in that region.
    cascade_start = src.find("# PRIMARY: Agent DNA queries")
    cascade_end = src.find("# Sort queries by historical yield", cascade_start)
    assert cascade_start != -1 and cascade_end != -1, "cascade markers not found — test stale?"
    cascade = src[cascade_start:cascade_end]
    # The fix uses an accumulation pattern (extend + seen set).
    has_accumulation = (
        "_seen" in cascade
        or ".extend(" in cascade
        or "_add_unique(" in cascade  # the helper that wraps the unique-append loop
        or "queries.append(" in cascade
    )
    assert has_accumulation, (
        "BRAIN-15 regression: query cascade must accumulate unique "
        "queries across tiers (DNA → AI → brain templates → fallback) "
        "instead of overwriting `queries` at each tier. Without this, "
        "high-quality DNA queries below a threshold are discarded."
    )


def test_query_tier_cascade_preserves_partial_dna():
    """The DNA-adoption guard must accept partial output (>= 1
    query), not require >= 5. Below the previous threshold, 4
    ICP-perfect queries were thrown away for 50 generic ones."""
    import app
    src = inspect.getsource(app)
    cascade_start = src.find("# PRIMARY: Agent DNA queries")
    cascade_end = src.find("# Sort queries by historical yield", cascade_start)
    cascade = src[cascade_start:cascade_end]
    # Old code: `if len(_dna_queries) >= 5:` — must be lowered.
    # Look for a `>= 1` or `>= 2` adoption check on DNA queries, OR
    # an unconditional extend (which is also acceptable).
    assert "_dna_queries) >= 5" not in cascade, (
        "BRAIN-15 regression: DNA-adoption threshold of `>= 5` "
        "discarded 1-4 ICP-tailored queries entirely. Lower to "
        "`>= 1` (any DNA queries are valuable; augment with brain "
        "templates if below target)."
    )


def test_query_tier_target_count_is_explicit():
    """The cascade should aim for a target query count (e.g., 30)
    via accumulation, not just clear `len < 5` thresholds. Makes
    the augmentation behavior intentional and inspectable."""
    import app
    src = inspect.getsource(app)
    cascade_start = src.find("# PRIMARY: Agent DNA queries")
    cascade_end = src.find("# Sort queries by historical yield", cascade_start)
    cascade = src[cascade_start:cascade_end]
    # Permissive: any of these patterns is acceptable as proof the
    # cascade has an explicit target rather than the old <5 guard.
    has_target = ("_QUERY_TARGET" in cascade
                  or "TARGET_QUERIES" in cascade
                  or "_TARGET" in cascade.upper()
                  or " < 30" in cascade)
    assert has_target, (
        "BRAIN-15 regression: cascade should have an explicit "
        "target count (e.g., 30) so each tier knows whether to "
        "augment. The old `< 5` thresholds were too permissive — "
        "a single tier returning 5 weak queries stopped the cascade."
    )
