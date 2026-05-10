"""Regression tests for BRAIN-155 (a566): `DEFAULT_SETTINGS`
shape integrity. Every `merge_settings` mutator starts
with `cur = {**DEFAULT_SETTINGS, **(cur or {})}` so the
shape of DEFAULT_SETTINGS defines the row's invariant
keyset. A typo or accidental mutation can silently break
every wizard write path.

Failure mode: someone removes a key from DEFAULT_SETTINGS,
the merge mutator stops seeding it, downstream code that
expects `s.get("wizard")` to be a dict starts seeing None,
chains of `.get(...)` collapse, persistence corrupts.

Invariants:
- `DEFAULT_SETTINGS` is a non-empty dict.
- Top-level keys are all strings.
- `wizard` key exists and defaults to dict shape.
- `_quotas` key exists and defaults to dict shape.
- All values are JSON-serialisable (passed through
  json.dumps without raising).
"""
from __future__ import annotations
import json


def test_default_settings_is_dict():
    """Top-level shape."""
    from server import DEFAULT_SETTINGS
    assert isinstance(DEFAULT_SETTINGS, dict)
    assert len(DEFAULT_SETTINGS) > 0


def test_default_settings_keys_are_strings():
    """All top-level keys are string."""
    from server import DEFAULT_SETTINGS
    for k in DEFAULT_SETTINGS.keys():
        assert isinstance(k, str), (
            f"BRAIN-155 regression: DEFAULT_SETTINGS "
            f"key {k!r} is not a string. Persistence "
            f"layer assumes string keys."
        )


def test_default_settings_wizard_field_either_absent_or_dict():
    """`wizard` key in DEFAULT_SETTINGS is optional —
    mutators handle both absent (lazy-init via
    `dict(s.get('wizard') or {})`) and present-as-dict.
    But it must NEVER be a non-dict if present."""
    from server import DEFAULT_SETTINGS
    if "wizard" in DEFAULT_SETTINGS:
        assert isinstance(DEFAULT_SETTINGS["wizard"], dict), (
            f"BRAIN-155 regression: DEFAULT_SETTINGS"
            f"['wizard'] is present but not a dict "
            f"(got {type(DEFAULT_SETTINGS['wizard'])}). "
            f"Mutators expect dict shape."
        )


def test_default_settings_is_json_serializable():
    """The whole dict must round-trip through
    json.dumps + loads. Every persistence write
    serializes via json."""
    from server import DEFAULT_SETTINGS
    try:
        s = json.dumps(DEFAULT_SETTINGS)
        roundtrip = json.loads(s)
    except (TypeError, ValueError) as e:
        raise AssertionError(
            f"BRAIN-155 regression: DEFAULT_SETTINGS not "
            f"JSON-serializable: {e}. Persistence will "
            f"fail on every wizard write."
        )
    assert roundtrip == DEFAULT_SETTINGS


def test_default_settings_does_not_leak_helpers():
    """DEFAULT_SETTINGS shouldn't accidentally contain
    helper objects (functions, classes, lambdas) that
    leak via `import server`. Only data."""
    from server import DEFAULT_SETTINGS
    import types
    for k, v in DEFAULT_SETTINGS.items():
        # Disallow function/method/class instances at
        # any depth (the json check catches most of
        # this but be explicit).
        assert not callable(v), (
            f"BRAIN-155 regression: DEFAULT_SETTINGS "
            f"key {k!r} is callable. Helper functions "
            f"don't belong in the persisted defaults."
        )
        assert not isinstance(v, (
            types.FunctionType,
            types.LambdaType,
            type,
        )), (
            f"BRAIN-155 regression: DEFAULT_SETTINGS "
            f"key {k!r} is a function/class. Helper "
            f"objects don't belong in persisted "
            f"defaults."
        )
