"""BRAIN-178: cli_memory.py helper invariant audit.

Pure-function helpers used by `huntova memory search` and
`huntova memory inspect`. Easy to test in isolation.

Pinned invariants:

1. `_safe_int` defensive on None / empty / non-numeric / float.
2. `_score_bucket` boundary cases (0, 4, 6, 8, 10, 11+, negative).
3. `_score_match` weights `org_name` 3x other fields.
4. `_score_match` is case-insensitive on the search terms (`hay` is
   lowered; terms in code are also lowered before passing in).
5. `_score_match` handles None / missing fields without crashing.
6. `_yaml_scalar` returns "~" for None.
7. `_yaml_scalar` returns lowercase booleans.
8. `_yaml_scalar` quotes strings containing reserved YAML chars.
9. `_yaml_scalar` uses block scalar (`|`) for multi-line strings.
10. `_yaml_scalar` escapes embedded double quotes.
"""
from __future__ import annotations


def test_safe_int_none():
    from cli_memory import _safe_int
    assert _safe_int(None) == 0


def test_safe_int_empty_string():
    from cli_memory import _safe_int
    assert _safe_int("") == 0


def test_safe_int_zero():
    from cli_memory import _safe_int
    assert _safe_int(0) == 0
    assert _safe_int("0") == 0


def test_safe_int_valid_int():
    from cli_memory import _safe_int
    assert _safe_int(42) == 42
    assert _safe_int("42") == 42


def test_safe_int_invalid_string():
    from cli_memory import _safe_int
    assert _safe_int("abc") == 0
    assert _safe_int("not-a-number") == 0


def test_safe_int_negative():
    from cli_memory import _safe_int
    assert _safe_int(-5) == -5
    assert _safe_int("-5") == -5


def test_safe_int_float_truncates():
    from cli_memory import _safe_int
    # int(7.9) → 7, but `int(x or 0)` with x=7.9 → 7.
    assert _safe_int(7.9) == 7


def test_score_bucket_strong():
    from cli_memory import _score_bucket
    assert _score_bucket(10) == "8-10 (strong)"
    assert _score_bucket(8) == "8-10 (strong)"
    assert _score_bucket(9) == "8-10 (strong)"


def test_score_bucket_qualified():
    from cli_memory import _score_bucket
    assert _score_bucket(6) == "6-7 (qualified)"
    assert _score_bucket(7) == "6-7 (qualified)"


def test_score_bucket_marginal():
    from cli_memory import _score_bucket
    assert _score_bucket(4) == "4-5 (marginal)"
    assert _score_bucket(5) == "4-5 (marginal)"


def test_score_bucket_weak():
    from cli_memory import _score_bucket
    assert _score_bucket(0) == "0-3 (weak)"
    assert _score_bucket(3) == "0-3 (weak)"
    # Negative falls into weak.
    assert _score_bucket(-1) == "0-3 (weak)"


def test_score_bucket_above_10():
    """Defensive: >10 falls into strong."""
    from cli_memory import _score_bucket
    assert _score_bucket(15) == "8-10 (strong)"


def test_score_match_org_name_weighted_3x():
    """Match in `org_name` counts as 3 hits; match in other fields as 1."""
    from cli_memory import _score_match
    lead = {"org_name": "acme corp", "why_fit": "good fit"}
    # "acme" appears once in org_name → score 3.
    assert _score_match(lead, ["acme"]) == 3

    lead2 = {"org_name": "other", "why_fit": "acme is mentioned here"}
    # "acme" appears once in why_fit → score 1.
    assert _score_match(lead2, ["acme"]) == 1


def test_score_match_multiple_terms():
    """Sum of all term hits."""
    from cli_memory import _score_match
    lead = {"org_name": "acme corp",
            "why_fit": "we want widgets and gadgets"}
    # acme in org_name (3) + widget in why_fit (1) + gadget in why_fit (1) = 5
    score = _score_match(lead, ["acme", "widget", "gadget"])
    assert score == 5


def test_score_match_case_insensitivity():
    """Internal `hay` is lowercased; caller is expected to lowercase
    terms — pin that the lowercased path returns hits."""
    from cli_memory import _score_match
    lead = {"org_name": "ACME CORP"}
    # Lowered term → matches lowered hay.
    assert _score_match(lead, ["acme"]) == 3
    # Upper-case term — won't match (hay is lowered).
    assert _score_match(lead, ["ACME"]) == 0


def test_score_match_handles_none_fields():
    from cli_memory import _score_match
    lead = {"org_name": None, "why_fit": "something"}
    # Must not crash.
    score = _score_match(lead, ["something"])
    assert score == 1


def test_score_match_handles_missing_fields():
    from cli_memory import _score_match
    lead = {}
    # Must not crash.
    assert _score_match(lead, ["anything"]) == 0


def test_score_match_zero_for_no_match():
    from cli_memory import _score_match
    lead = {"org_name": "acme corp"}
    assert _score_match(lead, ["nothing"]) == 0


def test_yaml_scalar_none_is_tilde():
    from cli_memory import _yaml_scalar
    assert _yaml_scalar(None) == "~"


def test_yaml_scalar_bool_lowercase():
    from cli_memory import _yaml_scalar
    assert _yaml_scalar(True) == "true"
    assert _yaml_scalar(False) == "false"


def test_yaml_scalar_int_no_quotes():
    from cli_memory import _yaml_scalar
    assert _yaml_scalar(42) == "42"
    assert _yaml_scalar(-5) == "-5"


def test_yaml_scalar_float_no_quotes():
    from cli_memory import _yaml_scalar
    assert _yaml_scalar(3.14) == "3.14"


def test_yaml_scalar_plain_string():
    from cli_memory import _yaml_scalar
    assert _yaml_scalar("hello") == "hello"


def test_yaml_scalar_quotes_reserved_chars():
    """Any of `:`, `#`, `&`, `*`, `!`, `|`, `>`, `%`, `@`, `` ` ``
    triggers double-quoting."""
    from cli_memory import _yaml_scalar
    assert _yaml_scalar("a:b").startswith('"')
    assert _yaml_scalar("# comment").startswith('"')
    assert _yaml_scalar("a&b").startswith('"')
    assert _yaml_scalar("a@b").startswith('"')
    assert _yaml_scalar("a!b").startswith('"')


def test_yaml_scalar_escapes_inner_double_quote():
    from cli_memory import _yaml_scalar
    out = _yaml_scalar('a:"b"')
    # Must contain `\"` (escaped) not just `"` (would break yaml).
    assert '\\"' in out


def test_yaml_scalar_block_scalar_for_multiline():
    from cli_memory import _yaml_scalar
    out = _yaml_scalar("line1\nline2\nline3")
    assert out.startswith("|\n")
    # Each line indented.
    assert "    line1" in out
    assert "    line2" in out
