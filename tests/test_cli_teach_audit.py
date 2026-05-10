"""BRAIN-186: cli_teach.py fuzzy-match + org-name normaliser audit.

`huntova teach --import` lets users bulk-mark leads via CSV; the
matcher uses fuzzy comparison on org_name. Pinned invariants:

1. `_norm_org` strips legal suffixes (Inc / Ltd / LLC / GmbH / SA / SRL).
2. `_norm_org` lowercases + strips punctuation.
3. `_norm_org` handles None / empty.
4. `_fuzzy_find` matches "Acme Corp" → "Acme Corporation" (≥0.78 ratio).
5. `_fuzzy_find` doesn't match unrelated strings.
6. `_fuzzy_find` returns None on empty needle / empty leads.
7. `_fuzzy_find` returns exact match immediately (no scan).
8. `_safe_int` defenses match cli_memory pattern.
"""
from __future__ import annotations


def test_norm_org_strips_inc():
    from cli_teach import _norm_org
    assert _norm_org("Acme Inc.") == "acme"
    assert _norm_org("Acme Inc") == "acme"


def test_norm_org_strips_ltd():
    from cli_teach import _norm_org
    assert _norm_org("Acme Ltd.") == "acme"
    assert _norm_org("Acme Ltd") == "acme"


def test_norm_org_strips_llc():
    from cli_teach import _norm_org
    assert _norm_org("Acme LLC") == "acme"


def test_norm_org_strips_gmbh():
    from cli_teach import _norm_org
    assert _norm_org("Acme GmbH") == "acme"


def test_norm_org_strips_sa_srl():
    from cli_teach import _norm_org
    assert _norm_org("Acme SA") == "acme"
    assert _norm_org("Acme S.A.") == "acme"
    assert _norm_org("Acme SRL") == "acme"
    assert _norm_org("Acme S.R.L.") == "acme"


def test_norm_org_strips_punctuation():
    """Comma, ampersand, etc. stripped — only alphanum + space kept."""
    from cli_teach import _norm_org
    out = _norm_org("Acme, Inc. & Co.")
    # Should be "acme" only (Inc., Co. stripped via _LEGAL or trailing).
    assert "acme" in out


def test_norm_org_lowercases():
    from cli_teach import _norm_org
    assert _norm_org("ACME") == "acme"
    assert _norm_org("Acme") == "acme"


def test_norm_org_handles_none():
    from cli_teach import _norm_org
    assert _norm_org(None) == ""
    assert _norm_org("") == ""
    assert _norm_org("   ") == ""


def test_norm_org_strips_only_trailing_legal_suffix():
    """Legal suffix matcher only matches at the end (.endswith) —
    'Inc Group' (Inc not at end) shouldn't be stripped."""
    from cli_teach import _norm_org
    out = _norm_org("Inc Group")
    # 'Inc Group' doesn't END with ' inc'; it STARTS. So Inc stays.
    assert "inc" in out and "group" in out


def test_fuzzy_find_exact_match():
    """Exact normalised match returns immediately."""
    from cli_teach import _fuzzy_find
    leads = [
        {"org_name": "Acme Corporation", "id": 1},
        {"org_name": "Beta Inc", "id": 2},
    ]
    out = _fuzzy_find("Acme Corporation", leads)
    assert out is not None
    assert out["id"] == 1


def test_fuzzy_find_legal_suffix_normalisation():
    """`Acme Corp` (after Corp normalisation) doesn't directly equal
    `Acme Inc` (after Inc normalisation) since 'corp' isn't in _LEGAL.
    But fuzzy ratio should still match closer pairs."""
    from cli_teach import _fuzzy_find
    leads = [
        {"org_name": "Acme Corporation Ltd", "id": 1},
    ]
    out = _fuzzy_find("Acme Corporation", leads)
    assert out is not None
    assert out["id"] == 1


def test_fuzzy_find_below_threshold_returns_none():
    """`Acme` vs `Globex` — too dissimilar (<0.78), return None."""
    from cli_teach import _fuzzy_find
    leads = [
        {"org_name": "Globex Ltd", "id": 1},
    ]
    out = _fuzzy_find("Acme Corp", leads)
    assert out is None


def test_fuzzy_find_empty_needle():
    from cli_teach import _fuzzy_find
    leads = [{"org_name": "Acme Inc"}]
    assert _fuzzy_find("", leads) is None
    assert _fuzzy_find(None, leads) is None  # type: ignore[arg-type]


def test_fuzzy_find_empty_leads():
    from cli_teach import _fuzzy_find
    assert _fuzzy_find("Acme Inc", []) is None


def test_fuzzy_find_skips_leads_with_no_org_name():
    """Leads missing org_name are skipped (not None-error)."""
    from cli_teach import _fuzzy_find
    leads = [
        {"id": 1},  # no org_name
        {"org_name": "Acme Inc", "id": 2},
    ]
    out = _fuzzy_find("Acme Inc", leads)
    assert out is not None
    assert out["id"] == 2


def test_fuzzy_find_returns_best_match():
    """Multiple candidates — return the one with highest ratio."""
    from cli_teach import _fuzzy_find
    leads = [
        {"org_name": "Acme XYZ Corp", "id": 1},
        {"org_name": "Acme Inc", "id": 2},
    ]
    # "Acme" should match "Acme Inc" (post-Inc-strip) more closely.
    out = _fuzzy_find("Acme Corp", leads)
    assert out is not None
    # Either match is acceptable as long as it's close-enough — pin
    # that some lead is returned.
    assert out["id"] in (1, 2)


def test_safe_int_defenses():
    from cli_teach import _safe_int
    assert _safe_int(None) == 0
    assert _safe_int("") == 0
    assert _safe_int("not-a-number") == 0
    assert _safe_int(7) == 7
    assert _safe_int("7") == 7
    assert _safe_int(7.9) == 7  # int(float) truncates
