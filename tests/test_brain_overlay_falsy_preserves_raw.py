"""Regression test for BRAIN-21 (a382): brain→wiz_data overlay
at app.py:7578-7583 used `_brain.get(key, default)` which returns
the brain's value even when it's an EMPTY list/string. Empty
overlays silently overwrite the user's raw wiz_data.

Failure path: user trains the brain with industries that all hit
_clean()'s _VAGUE filter (e.g., "consulting", "agency"). Brain's
`preferred_industries = []`. The overlay then overwrites
`_wiz_data["icp_industries"]` with `[]`, losing the user's raw
input. Same class as GPT-5.4's "or chain graveyard" pattern from
the audit.

Fix: use `_brain.get(key) or _wiz_data.get(...)` — Python's `or`
short-circuits on falsy, so empty brain values fall through to
raw wiz_data instead of clobbering.
"""
from __future__ import annotations
import inspect


def test_overlay_uses_or_chain_for_falsy_safety():
    """Source-level invariant: the brain→wiz_data overlay block
    must use `or` chains, not `.get(key, default)`, so empty brain
    values fall through to raw wiz_data instead of clobbering."""
    import app
    src = inspect.getsource(app)
    region_start = src.find("# Use brain fields for query generation instead of raw wizard data")
    assert region_start != -1, "overlay marker not found — test stale?"
    region = src[region_start:region_start + 800]
    # The fix uses `_brain.get("...") or _wiz_data.get(...)` pattern.
    # Pre-fix used `_brain.get("...", _wiz_data.get(...))` — `.get` with
    # default doesn't fall through on empty brain values.
    has_or_chain = "_brain.get(" in region and " or _wiz_data" in region
    assert has_or_chain, (
        "BRAIN-21 regression: brain→wiz_data overlay must use "
        "`_brain.get(key) or _wiz_data.get(...)` pattern. The "
        "previous `.get(key, default)` form returns brain's empty "
        "value (falsy but present) instead of falling through, "
        "silently overwriting the user's raw wiz_data when the "
        "brain's normalized field is empty (e.g. all industries "
        "filtered as vague)."
    )
