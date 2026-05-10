"""Regression test for BRAIN-27 (a388): the learning-profile
avoided-pattern check at app.py:7173 concatenated `data.get(key, "")`
results. `.get(key, "")` returns the value when key is present —
even if that value is None — so `None + " " + ...` crashed with
`unsupported operand type(s) for +: 'NoneType' and 'str'`.

The fix: `(data.get(key) or "")` chain — Python `or` short-circuits
on None.
"""
from __future__ import annotations
import inspect


def test_org_low_concat_uses_or_chain_not_get_default():
    """Source-level: the concatenation must use `or ""` chains
    instead of `.get(key, "")` because the latter doesn't fall
    through on None values."""
    import app
    src = inspect.getsource(app)
    # Find the specific line
    idx = src.find('_org_low = ')
    assert idx != -1
    region = src[idx:idx + 400]
    # The fix must use `or ""` for every concatenated piece.
    assert "data.get(\"org_name\") or \"\"" in region or "data.get('org_name') or ''" in region, (
        "BRAIN-27 regression: concatenation must guard against None "
        "via `(data.get(key) or \"\")`. `.get(key, \"\")` returns None "
        "when value is None and crashes the concat."
    )
