"""Regression tests for BRAIN-PROD-6 (a568): the Settings
UI's "clear-and-save" path must actually clear the value
on the back-end, not silently no-op.

Failure mode (Enzo, 2026-05-04: "feels overally bugged in
some places inside the settings"):

The Engine and Defaults tabs in the Settings panel each
had a save handler that only included a field in the POST
patch when the input was non-empty. So when a user cleared
the model override, temperature, max-leads, or countries
field and clicked Save, the patch shipped without that
key — `api_save_settings` left the prior persisted value
untouched, the toast said "Saved", but the next page load
re-rendered the old value. Symptom: the form felt buggy /
broken because edits were silently ignored.

Twin fix:

1. Front-end (`templates/jarvis.html` `_renderSettingsEngine`
   + `_renderSettingsDefaults`): always include the field
   in the patch, sending `null` / `""` / `[]` to mean clear.

2. Back-end (`server.api_save_settings`): when
   `preferred_temperature` or `default_max_leads` is `null`
   or empty string, reset to the `DEFAULT_SETTINGS` value
   instead of silently dropping the write. Empty list for
   `default_countries` was already handled correctly.

Source-level invariants this test pins:

- `api_save_settings` has a null/"" → DEFAULT_SETTINGS branch
  for `default_max_leads`.
- Same for `preferred_temperature`.
- The front-end Engine + Defaults save handlers always
  include the cleared fields in the patch (no `if value`
  guard that would skip them).
"""
from __future__ import annotations
import inspect
import re
from pathlib import Path


# ── back-end invariants ──

def test_api_save_settings_default_max_leads_accepts_null_clear():
    """When the client posts `default_max_leads: null` (or empty
    string), the back-end must reset to the DEFAULT_SETTINGS
    value, not silently drop the write."""
    from server import api_save_settings
    src = inspect.getsource(api_save_settings)
    # The fix branches on `is None or … strip() == ""` and
    # writes `DEFAULT_SETTINGS.get("default_max_leads", …)`.
    assert 'DEFAULT_SETTINGS.get("default_max_leads"' in src, (
        "BRAIN-PROD-6 regression: api_save_settings must "
        "reset default_max_leads to the DEFAULT_SETTINGS "
        "value when the patch sends null/empty. Without "
        "this, clearing the field in the UI is a silent "
        "no-op and the form looks bugged."
    )


def test_api_save_settings_preferred_temperature_accepts_null_clear():
    """When the client posts `preferred_temperature: null`
    (or empty string), the back-end must reset to the
    DEFAULT_SETTINGS value, not silently drop the write."""
    from server import api_save_settings
    src = inspect.getsource(api_save_settings)
    assert 'DEFAULT_SETTINGS.get("preferred_temperature"' in src, (
        "BRAIN-PROD-6 regression: api_save_settings must "
        "reset preferred_temperature to the DEFAULT_SETTINGS "
        "value when the patch sends null/empty. Without "
        "this, clearing the temperature field in the UI is "
        "a silent no-op."
    )


def test_default_max_leads_default_is_sensible():
    """The fallback the writer reads must be a real int in
    a sensible range. If DEFAULT_SETTINGS gets accidentally
    changed to a string or out-of-range value, the reset
    branch would corrupt the row."""
    from server import DEFAULT_SETTINGS
    v = DEFAULT_SETTINGS.get("default_max_leads")
    assert isinstance(v, int) and 1 <= v <= 500, (
        f"BRAIN-PROD-6 regression: DEFAULT_SETTINGS"
        f"['default_max_leads'] = {v!r}; must be an int in "
        f"[1, 500] so the null-clear reset branch produces "
        f"a sane row."
    )


def test_preferred_temperature_default_is_sensible():
    """Same guard for `preferred_temperature`."""
    from server import DEFAULT_SETTINGS
    v = DEFAULT_SETTINGS.get("preferred_temperature")
    assert isinstance(v, (int, float)) and 0.0 <= float(v) <= 1.0, (
        f"BRAIN-PROD-6 regression: DEFAULT_SETTINGS"
        f"['preferred_temperature'] = {v!r}; must be a "
        f"float in [0.0, 1.0]."
    )


# ── front-end invariants (source grep on jarvis.html) ──

_JARVIS = Path(__file__).resolve().parent.parent / "templates" / "jarvis.html"


def _read_jarvis() -> str:
    return _JARVIS.read_text(encoding="utf-8")


def test_engine_save_always_sends_preferred_model():
    """`_renderSettingsEngine` must include `preferred_model`
    in the patch unconditionally (so empty input clears the
    server-side override)."""
    src = _read_jarvis()
    # Grab the Engine save handler region: from
    # `_renderSettingsEngine` to the end of its save click
    # handler. A 6 KiB window is plenty.
    idx = src.find("_renderSettingsEngine")
    assert idx > 0, "Could not locate _renderSettingsEngine in jarvis.html"
    region = src[idx: idx + 6000]
    # The pre-fix bug was: `if (modelInput.value !== '')
    # patch.preferred_model = …`. The fix unconditionally
    # writes `preferred_model: modelInput.value.trim()` into
    # the patch literal.
    assert "preferred_model: modelInput.value.trim()" in region, (
        "BRAIN-PROD-6 regression: _renderSettingsEngine "
        "must always send `preferred_model` in the patch "
        "(empty string clears the override). Pre-a568, an "
        "`if (modelInput.value !== '')` guard made clearing "
        "the field a silent no-op."
    )


def test_engine_save_always_sends_preferred_temperature():
    """`_renderSettingsEngine` must include
    `preferred_temperature` in the patch unconditionally
    (empty input → null → server resets to default)."""
    src = _read_jarvis()
    idx = src.find("_renderSettingsEngine")
    region = src[idx: idx + 6000]
    # Look for the patch literal having
    # `preferred_temperature: tempInput.value === ''`.
    assert "preferred_temperature: tempInput.value === ''" in region, (
        "BRAIN-PROD-6 regression: _renderSettingsEngine "
        "must always send `preferred_temperature` in the "
        "patch — null when cleared, numeric otherwise. "
        "Without this, clearing the temperature field is a "
        "silent no-op."
    )


def test_defaults_save_always_sends_default_max_leads():
    """`_renderSettingsDefaults` must include
    `default_max_leads` in the patch unconditionally."""
    src = _read_jarvis()
    idx = src.find("_renderSettingsDefaults")
    assert idx > 0, "Could not locate _renderSettingsDefaults in jarvis.html"
    region = src[idx: idx + 4000]
    assert "default_max_leads: maxLeads.value === ''" in region, (
        "BRAIN-PROD-6 regression: _renderSettingsDefaults "
        "must always send `default_max_leads` (null when "
        "cleared). Pre-a568, `if (maxLeads.value)` made "
        "clearing the field a silent no-op."
    )


def test_defaults_save_always_sends_default_countries():
    """`_renderSettingsDefaults` must include
    `default_countries` in the patch unconditionally."""
    src = _read_jarvis()
    idx = src.find("_renderSettingsDefaults")
    region = src[idx: idx + 4000]
    assert "default_countries: countries.value === ''" in region, (
        "BRAIN-PROD-6 regression: _renderSettingsDefaults "
        "must always send `default_countries` (empty array "
        "when cleared). Pre-a568, `if (countries.value !== "
        "'')` made clearing the textarea a silent no-op."
    )


def test_engine_save_no_legacy_conditional_model_guard():
    """Source-level guardrail: the legacy
    `if (modelInput.value !== '') patch.preferred_model = …`
    pattern must NOT come back, otherwise the silent-no-op
    bug regresses."""
    src = _read_jarvis()
    idx = src.find("_renderSettingsEngine")
    region = src[idx: idx + 6000]
    # Permissive regex to catch whitespace variants but pin
    # the legacy bug shape.
    legacy = re.compile(
        r"if\s*\(\s*modelInput\.value\s*!==\s*''\s*\)\s*patch\.preferred_model"
    )
    assert not legacy.search(region), (
        "BRAIN-PROD-6 regression: the legacy "
        "`if (modelInput.value !== '') patch.preferred_model"
        " = …` guard is back. Clearing the model override "
        "field will silently no-op. Always include "
        "preferred_model in the patch."
    )
