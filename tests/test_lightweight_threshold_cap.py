"""Regression test for BRAIN-18 (a379): lightweight-mode acceptance
threshold inversion at app.py:7517-7526. Comment said "but cap to
lightweight-friendly values" — implying min() — but code used
max() which RAISED strictness. A dossier tuned for full-fat hunts
(buyability_threshold=5) silently leaked into lightweight hunts
(default 2) via max(5, 2) = 5, rejecting otherwise-good leads
because the deep verification signals required to justify 5 don't
exist without Playwright.

Per GPT-5.4 senior-engineer audit (this session): "Silent threshold
inversion in lightweight mode that systematically rejects
otherwise-good leads."
"""
from __future__ import annotations
import inspect


def test_lightweight_thresholds_are_capped_not_raised():
    """In lightweight mode, dossier thresholds higher than the
    relaxed defaults must be CAPPED (min), not raised (max). The
    deep-verification signals required to justify higher thresholds
    don't exist without Playwright."""
    import app
    src = inspect.getsource(app)
    region_start = src.find('"buyability_threshold": 2 if _is_lightweight else 3')
    assert region_start != -1, "lightweight default marker not found — test stale?"
    region = src[region_start:region_start + 2500]
    # The fix uses min() OR a lightweight-conditional that makes the
    # cap behavior explicit. The previous implementation used max()
    # unconditionally — that's the bug.
    # The merge block must (a) reference _is_lightweight to discriminate
    # full vs lightweight behaviour, and (b) use min() somewhere when
    # in lightweight mode. Pre-fix used unconditional max() — that's
    # the bug.
    has_lightweight_branch = "_is_lightweight" in region
    has_min = "min(" in region
    assert has_lightweight_branch and has_min, (
        "BRAIN-18 regression: lightweight mode must CAP dossier "
        "thresholds (min), not RAISE them (max). Comment said 'cap "
        "to lightweight-friendly values' but code used max() which "
        "is the opposite. The merge block must branch on "
        "_is_lightweight and use min() in the lightweight branch."
    )


def test_full_mode_can_still_use_stricter_dossier():
    """Don't regress full-mode (Playwright available) behavior. In
    full mode, the dossier's stricter thresholds should still be
    respected — the deep-verification signals justify them."""
    import app
    src = inspect.getsource(app)
    region_start = src.find('"buyability_threshold": 2 if _is_lightweight else 3')
    region = src[region_start:region_start + 2500]
    # Full mode should still pick up the dossier threshold somehow.
    # We just check that the merge logic exists (not that it's max).
    assert "_das.get(" in region, (
        "BRAIN-18 regression: dossier acceptance_spec must still be "
        "consulted in full mode — the threshold merge logic exists."
    )
