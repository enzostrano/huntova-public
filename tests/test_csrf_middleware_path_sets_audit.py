"""Regression tests for BRAIN-154 (a565): CSRF
middleware path-set integrity. The middleware uses
three module-scope path sets:
- `CSRF_EXEMPT_PATHS` (skip CSRF token check)
- `_CSRF_EXEMPT_ALSO_ORIGIN_EXEMPT` (skip Origin too)
- `_WIZARD_DESTRUCTIVE_ORIGIN_GATED_PATHS` (extra Origin gate)

Failure mode: a typo or set-mutation regression silently
opens a CSRF hole. E.g. `/api/wizard/reset` accidentally
ending up in `CSRF_EXEMPT_PATHS` would make destructive
reset CSRF-bypass-able.

Invariants:
- Each set is a non-empty set/frozenset of strings.
- `_CSRF_EXEMPT_ALSO_ORIGIN_EXEMPT` is a SUBSET of
  `CSRF_EXEMPT_PATHS` (can't be origin-exempt without
  being CSRF-exempt first).
- `_WIZARD_DESTRUCTIVE_ORIGIN_GATED_PATHS` and
  `CSRF_EXEMPT_PATHS` are DISJOINT (a destructive
  endpoint that's also CSRF-exempt would be wide open).
- Critical destructive endpoints (`/api/wizard/reset`,
  `/api/wizard/start-retrain`) are NOT in any exemption
  set.
"""
from __future__ import annotations


def test_csrf_path_sets_exist_and_nonempty():
    """All three sets must exist as non-empty
    string collections."""
    import server as _s
    for name in (
        "CSRF_EXEMPT_PATHS",
        "_CSRF_EXEMPT_ALSO_ORIGIN_EXEMPT",
        "_WIZARD_DESTRUCTIVE_ORIGIN_GATED_PATHS",
    ):
        s = getattr(_s, name, None)
        assert s is not None, (
            f"BRAIN-154 regression: server must expose "
            f"`{name}` set."
        )
        assert isinstance(s, (set, frozenset)), (
            f"BRAIN-154 regression: `{name}` must be a "
            f"set/frozenset, got {type(s)}."
        )
        assert len(s) > 0, (
            f"BRAIN-154 regression: `{name}` is empty. "
            f"Either intentionally cleared (must update "
            f"this test) or accidentally regressed."
        )


def test_origin_exempt_is_subset_of_csrf_exempt():
    """An endpoint can only be Origin-exempt if it's
    already CSRF-exempt — the middleware structure
    enforces this, but the set membership should
    too."""
    import server as _s
    csrf_exempt = _s.CSRF_EXEMPT_PATHS
    origin_exempt = _s._CSRF_EXEMPT_ALSO_ORIGIN_EXEMPT
    extra = origin_exempt - csrf_exempt
    assert not extra, (
        f"BRAIN-154 regression: paths in "
        f"`_CSRF_EXEMPT_ALSO_ORIGIN_EXEMPT` but not in "
        f"`CSRF_EXEMPT_PATHS`: {extra}. Origin-exempt "
        f"requires CSRF-exempt; the middleware skips "
        f"the Origin check ONLY for CSRF-exempt paths."
    )


def test_destructive_paths_not_csrf_exempt():
    """Destructive endpoints (where Origin gate runs)
    must NOT be CSRF-exempt. Otherwise the destructive
    set is in the wrong sublattice — its members
    bypass both checks, defeating the purpose."""
    import server as _s
    destructive = _s._WIZARD_DESTRUCTIVE_ORIGIN_GATED_PATHS
    csrf_exempt = _s.CSRF_EXEMPT_PATHS
    overlap = destructive & csrf_exempt
    assert not overlap, (
        f"BRAIN-154 regression: destructive paths also "
        f"in CSRF_EXEMPT_PATHS: {overlap}. A destructive "
        f"endpoint must NOT be CSRF-exempt."
    )


def test_critical_destructive_endpoints_in_destructive_set():
    """The two BRAIN-114 endpoints must remain in the
    destructive set."""
    import server as _s
    destructive = _s._WIZARD_DESTRUCTIVE_ORIGIN_GATED_PATHS
    critical = {
        "/api/wizard/reset",
        "/api/wizard/start-retrain",
    }
    missing = critical - destructive
    assert not missing, (
        f"BRAIN-154 regression: critical destructive "
        f"endpoints missing from gate set: {missing}. "
        f"BRAIN-114 contract violated."
    )


def test_wizard_critical_paths_not_exempted():
    """Critical wizard mutators must not be in any
    exemption set."""
    import server as _s
    critical_must_be_protected = {
        "/api/wizard/complete",
        "/api/wizard/save-progress",
        "/api/wizard/scan",
        "/api/wizard/generate-phase5",
        "/api/wizard/assist",
        "/agent/control",
    }
    csrf_exempt = _s.CSRF_EXEMPT_PATHS
    leaked = critical_must_be_protected & csrf_exempt
    assert not leaked, (
        f"BRAIN-154 regression: wizard mutators "
        f"accidentally CSRF-exempt: {leaked}. These "
        f"endpoints MUST require the double-submit "
        f"CSRF token."
    )
