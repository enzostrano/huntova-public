"""BRAIN-179: cli_benchmark.py helper invariant audit.

Pure helpers in `cli_benchmark.py` (synthetic-hunt provider quality
benchmark): token counter, cost estimator, JSON-score parser,
percentile calculator. Easy to test in isolation; cover the
parse_score gauntlet that protects benchmark stability.

Pinned invariants:

1. `_approx_tokens` returns ≥1 for any non-empty input.
2. `_approx_tokens` returns 1 for empty / None.
3. `_est_cost` returns 0.0 for unknown provider (no _PRICING entry).
4. `_pct` percentile correctness on small samples.
5. `_pct` 0% / 100% boundary returns min/max.
6. `_pct` empty list returns 0.0 (no crash).
7. `_parse_score` returns None for empty / non-JSON / partial-key
   responses.
8. `_parse_score` strips ```json fences correctly.
9. `_parse_score` clamps each score to 0..10.
10. `_parse_score` requires ≥3 of 5 score keys (rejects chatty
    replies with stray `{...}`).
"""
from __future__ import annotations


def test_approx_tokens_nonempty_text():
    from cli_benchmark import _approx_tokens
    assert _approx_tokens("hello world", "anthropic") >= 1


def test_approx_tokens_empty_returns_at_least_1():
    from cli_benchmark import _approx_tokens
    # Defensive: max(1, …) guards against div-by-zero / 0-token returns.
    assert _approx_tokens("", "anthropic") >= 1
    assert _approx_tokens(None, "anthropic") >= 1  # type: ignore[arg-type]


def test_approx_tokens_unknown_provider_uses_default():
    """Falls back to default chars-per-token (4.0) when provider not
    in _CHARS_PER_TOKEN."""
    from cli_benchmark import _approx_tokens
    out = _approx_tokens("a" * 40, "totally-unknown-provider")
    # 40 chars / 4.0 = 10 tokens.
    assert out == 10


def test_est_cost_unknown_provider_zero():
    """Unknown provider has no pricing → cost is 0."""
    from cli_benchmark import _est_cost
    assert _est_cost("totally-unknown", "input text", "output text") == 0.0


def test_est_cost_known_provider_positive():
    """A known provider returns a positive cost."""
    from cli_benchmark import _est_cost, _PRICING
    # Pick the first provider with non-zero pricing.
    candidates = [p for p, (i, o) in _PRICING.items() if i > 0 or o > 0]
    if not candidates:
        return  # no priced providers — nothing to assert
    cost = _est_cost(candidates[0], "input text", "output text")
    assert cost > 0


def test_pct_empty_returns_zero():
    from cli_benchmark import _pct
    assert _pct([], 0.5) == 0.0


def test_pct_single_element():
    from cli_benchmark import _pct
    # Single-element list: every percentile is that element.
    assert _pct([42.0], 0.0) == 42.0
    assert _pct([42.0], 0.5) == 42.0
    assert _pct([42.0], 1.0) == 42.0


def test_pct_min_max():
    from cli_benchmark import _pct
    values = [10.0, 20.0, 30.0, 40.0, 50.0]
    # 0% = min, 100% = max.
    assert _pct(values, 0.0) == 10.0
    assert _pct(values, 1.0) == 50.0


def test_pct_median():
    from cli_benchmark import _pct
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    # 50th percentile rounds nearest-rank.
    assert _pct(values, 0.5) == 3.0


def test_pct_sorts_input():
    """`_pct` should sort the values internally — caller doesn't need
    to pre-sort."""
    from cli_benchmark import _pct
    out_sorted = _pct([1.0, 2.0, 3.0, 4.0, 5.0], 0.5)
    out_unsorted = _pct([5.0, 1.0, 3.0, 2.0, 4.0], 0.5)
    assert out_sorted == out_unsorted


def test_parse_score_returns_none_for_empty():
    from cli_benchmark import _parse_score
    assert _parse_score("") is None
    assert _parse_score(None) is None  # type: ignore[arg-type]
    assert _parse_score("   ") is None


def test_parse_score_returns_none_for_non_json():
    from cli_benchmark import _parse_score
    assert _parse_score("just some text, no JSON anywhere") is None


def test_parse_score_strips_markdown_fences():
    """JSON wrapped in ```json … ``` fences (LLM markdown habit) is
    extracted correctly."""
    from cli_benchmark import _parse_score
    raw = '```json\n{"fit_score":8,"buyability_score":7,"reachability_score":6,"service_opportunity_score":5,"timing_score":4}\n```'
    out = _parse_score(raw)
    assert out is not None
    assert out["fit_score"] == 8


def test_parse_score_clamps_to_0_10():
    """Out-of-range scores clamp to [0, 10]."""
    from cli_benchmark import _parse_score
    raw = '{"fit_score":99,"buyability_score":-5,"reachability_score":7,"service_opportunity_score":15,"timing_score":3}'
    out = _parse_score(raw)
    assert out is not None
    assert out["fit_score"] == 10  # clamped from 99
    assert out["buyability_score"] == 0  # clamped from -5
    assert out["reachability_score"] == 7
    assert out["service_opportunity_score"] == 10  # clamped from 15
    assert out["timing_score"] == 3


def test_parse_score_handles_float_scores():
    """LLMs sometimes return `7.5` — coerce via float→int (truncate)."""
    from cli_benchmark import _parse_score
    raw = '{"fit_score":7.5,"buyability_score":6,"reachability_score":5,"service_opportunity_score":4,"timing_score":3}'
    out = _parse_score(raw)
    assert out is not None
    assert out["fit_score"] == 7  # int(float(7.5)) = 7


def test_parse_score_requires_at_least_3_of_5_keys():
    """If <3 of the 5 score keys are present, return None — guards
    against stray `{...}` in chatty replies parsing to all-zeros."""
    from cli_benchmark import _parse_score
    # 2 keys → reject.
    raw_2 = '{"fit_score":8,"buyability_score":7}'
    assert _parse_score(raw_2) is None

    # 3 keys → accept.
    raw_3 = '{"fit_score":8,"buyability_score":7,"reachability_score":6}'
    out = _parse_score(raw_3)
    assert out is not None
    # Missing keys default to 0.
    assert out["timing_score"] == 0


def test_parse_score_handles_non_dict():
    """JSON array or scalar — must reject."""
    from cli_benchmark import _parse_score
    assert _parse_score("[1, 2, 3]") is None
    assert _parse_score("42") is None


def test_parse_score_handles_unparseable_score_value():
    """A score field with a non-numeric value (`"high"`) defaults to 0."""
    from cli_benchmark import _parse_score
    raw = '{"fit_score":"high","buyability_score":7,"reachability_score":6,"service_opportunity_score":5,"timing_score":4}'
    out = _parse_score(raw)
    assert out is not None
    assert out["fit_score"] == 0  # unparseable → 0


def test_parse_score_strips_backticks_only_fence():
    """Some LLMs return ``` ... ``` without `json` keyword."""
    from cli_benchmark import _parse_score
    raw = '```\n{"fit_score":8,"buyability_score":7,"reachability_score":6,"service_opportunity_score":5,"timing_score":4}\n```'
    out = _parse_score(raw)
    assert out is not None
    assert out["fit_score"] == 8
