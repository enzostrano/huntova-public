"""Regression tests for BRAIN-151 (a562): blanket
audit of `_RATE_BUCKETS` config — every entry has a
positive-int window + positive-int max_calls, sane
bounds (window in [10, 3600], max_calls in [1, 1000]),
and the dict isn't accidentally empty.

Failure mode (Per Huntova engineering review on
config integrity):

`_RATE_BUCKETS` is the single source of truth for
per-endpoint rate-limit budgets across BRAIN-91/112/
113/142/144 et al. A typo or accidental empty value
on any entry breaks the rate-limit gate for that
endpoint silently — `_check_ai_rate` falls back to
defaults that may be wildly wrong.

Pre-fix: each new bucket addition is reviewed
manually but no automated guard ensures the shape.
A future PR adding `"new_endpoint": (60, 0)` would
register a 0-call cap (instant block) without
anything catching it.

Invariants:
- `_RATE_BUCKETS` dict has at least 8 entries (the
  baseline buckets) — a regression that empties the
  dict gets caught.
- Every entry's window is a positive int in [10, 3600]
  seconds.
- Every entry's max_calls is a positive int in [1, 1000].
- Bucket names are lowercase + underscore-separated.
"""
from __future__ import annotations


def test_rate_buckets_dict_has_baseline_entries():
    """Sanity: dict has at least the documented
    baseline buckets."""
    from server import _RATE_BUCKETS
    assert isinstance(_RATE_BUCKETS, dict)
    assert len(_RATE_BUCKETS) >= 8, (
        f"BRAIN-151 regression: _RATE_BUCKETS has "
        f"only {len(_RATE_BUCKETS)} entries; expected "
        f"≥ 8. The dict may have been accidentally "
        f"truncated."
    )


def test_baseline_buckets_present():
    """Critical buckets must exist."""
    from server import _RATE_BUCKETS
    critical = {
        "ai", "wizard_save_progress", "wizard_scan",
        "wizard_phase5", "wizard_complete", "wizard_assist",
        "wizard_reset", "wizard_status",
    }
    missing = critical - set(_RATE_BUCKETS.keys())
    assert not missing, (
        f"BRAIN-151 regression: critical rate buckets "
        f"missing from _RATE_BUCKETS: {missing}."
    )


def test_every_bucket_value_is_window_max_pair():
    """Every entry must be a (window, max_calls)
    tuple/list."""
    from server import _RATE_BUCKETS
    for name, value in _RATE_BUCKETS.items():
        assert isinstance(value, (tuple, list)) and len(value) == 2, (
            f"BRAIN-151 regression: bucket {name!r} "
            f"value {value!r} is not a (window, "
            f"max_calls) pair."
        )


def test_every_bucket_window_is_sane():
    """Window in [10, 3600] seconds."""
    from server import _RATE_BUCKETS
    for name, (window, _max_calls) in _RATE_BUCKETS.items():
        assert isinstance(window, int) and window > 0, (
            f"BRAIN-151 regression: bucket {name!r} "
            f"window {window!r} is not a positive int."
        )
        assert 10 <= window <= 3600, (
            f"BRAIN-151 regression: bucket {name!r} "
            f"window {window} outside sane bounds "
            f"[10, 3600] seconds."
        )


def test_every_bucket_max_calls_is_sane():
    """max_calls in [1, 1000]."""
    from server import _RATE_BUCKETS
    for name, (_window, max_calls) in _RATE_BUCKETS.items():
        assert isinstance(max_calls, int) and max_calls > 0, (
            f"BRAIN-151 regression: bucket {name!r} "
            f"max_calls {max_calls!r} is not a positive "
            f"int — accidental 0 would mean instant "
            f"block on first call."
        )
        assert 1 <= max_calls <= 1000, (
            f"BRAIN-151 regression: bucket {name!r} "
            f"max_calls {max_calls} outside sane bounds "
            f"[1, 1000]."
        )


def test_bucket_names_are_normalized():
    """Lowercase + underscore — no accidental hyphens
    or camelCase that'd diverge from the convention."""
    from server import _RATE_BUCKETS
    for name in _RATE_BUCKETS.keys():
        assert isinstance(name, str)
        assert name == name.lower(), (
            f"BRAIN-151 regression: bucket name {name!r} "
            f"contains uppercase. Convention is "
            f"lowercase_underscore."
        )
        # Hyphen is also a non-conventional separator.
        assert "-" not in name, (
            f"BRAIN-151 regression: bucket name {name!r} "
            f"contains hyphen. Convention is underscore."
        )
