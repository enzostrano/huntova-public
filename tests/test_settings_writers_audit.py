"""Regression tests for BRAIN-156 (a569): drift detection
between `api_save_settings` writer and `DEFAULT_SETTINGS`
template. Settings keys persisted by the writer but
absent from defaults silently lose typing/sanity-check
benefits the merge mutator's `{**DEFAULT_SETTINGS, **cur}`
spread normally provides.

Failure mode: `/api/settings` accepts a new field, the
form persists it, but DEFAULT_SETTINGS doesn't include
it. On a fresh user (no settings row yet), `s.get(field)`
returns None instead of the documented default → frontend
sees null → form renders blank → user thinks the toggle
was reset.

Invariants:
- The writer's whitelisted-string-fields tuple in
  `api_save_settings` (BRAIN- a247 expanded list) is at
  least N keys long (sanity bound).
- Every key in that whitelist that's a "default-bearing"
  scalar should have a corresponding entry in
  DEFAULT_SETTINGS OR be intentionally None-default
  (e.g. `from_email`).
- The whitelist is a tuple/list (not a generator).
"""
from __future__ import annotations
import inspect
import re


def test_settings_writer_whitelist_present():
    """Source-level: api_save_settings has a
    multi-key allowlist (more than 5 string fields
    enumerated)."""
    from server import api_save_settings
    src = inspect.getsource(api_save_settings)
    # Just count quoted lowercase identifiers — a
    # writer with a real whitelist will have many.
    keys = re.findall(r'"([a-z][a-z_0-9]+)"', src)
    assert len(set(keys)) >= 5, (
        f"BRAIN-156 regression: api_save_settings has "
        f"only {len(set(keys))} unique quoted string "
        f"keys — looks like the whitelist was "
        f"truncated. Form persistence will silently "
        f"drop most fields."
    )


def test_settings_writer_whitelist_has_baseline_keys():
    """Source-level: critical fields stay in the
    whitelist."""
    from server import api_save_settings
    src = inspect.getsource(api_save_settings)
    critical = {
        "preferred_provider",
        "preferred_model",
        "default_tone",
        "from_name",
        "from_email",
        "booking_url",
    }
    missing = {k for k in critical if f'"{k}"' not in src}
    assert not missing, (
        f"BRAIN-156 regression: api_save_settings "
        f"missing critical whitelist keys: {missing}. "
        f"Forms posting these fields will silently "
        f"drop them on save."
    )


def test_default_settings_has_baseline_provider_keys():
    """The provider-pick fields must default to
    something sensible (or be explicitly absent if
    None is the legitimate first-run state)."""
    from server import DEFAULT_SETTINGS
    # `preferred_provider` may be absent on fresh
    # install (no provider chosen yet) — but if it's
    # present, must be a string. Same for
    # preferred_model.
    for k in ("preferred_provider", "preferred_model"):
        if k in DEFAULT_SETTINGS:
            v = DEFAULT_SETTINGS[k]
            assert isinstance(v, (str, type(None))), (
                f"BRAIN-156 regression: DEFAULT_SETTINGS"
                f"[{k!r}] = {v!r} ({type(v).__name__}); "
                f"must be str or None."
            )


def test_settings_writer_does_not_silent_drop_on_unknown_keys():
    """Source-level: api_save_settings shouldn't have
    a sneaky path that silently accepts arbitrary
    unknown keys into the row. Closed schema is
    BRAIN-73's contract for wizard answers; settings
    POST should follow similar discipline."""
    from server import api_save_settings
    src = inspect.getsource(api_save_settings)
    # Confirm there's NO `for k, v in body.items():
    # s[k] = v` pattern (that would silently accept
    # unknown keys).
    assert "for k, v in body.items()" not in src and "for k,v in body.items()" not in src, (
        "BRAIN-156 regression: api_save_settings has "
        "an open-iteration pattern that may silently "
        "persist arbitrary unknown keys. Closed schema "
        "should be the rule."
    )


def test_default_settings_has_no_orphaned_pii_fields():
    """The `from_email`, `phone`, `smtp_pass` style
    fields must default to empty string or empty/None,
    NOT to a real value baked into the codebase."""
    from server import DEFAULT_SETTINGS
    for k in ("from_email", "phone", "smtp_pass", "smtp_user"):
        if k in DEFAULT_SETTINGS:
            v = DEFAULT_SETTINGS[k]
            assert v in (None, "", 0, False), (
                f"BRAIN-156 regression: DEFAULT_SETTINGS"
                f"[{k!r}] = {v!r} — looks like a real "
                f"value baked into defaults. PII/secret "
                f"fields must default to empty/None."
            )
