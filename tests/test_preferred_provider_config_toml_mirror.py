"""Regression test for BRAIN-PROD-2 (a507): /api/settings → Engine save
must mirror `preferred_provider` to ~/.config/huntova/config.toml.

Bug surface: in local mode, `providers.get_provider()` reads
`preferred_provider` from config.toml via `_load_local_settings()`, NOT
from the user_settings DB row. The Engine tab in Settings was writing
`preferred_provider` to the DB only — so users who picked "OpenAI" in
Settings → Engine saw chat keep routing through whatever provider
config.toml had pinned previously (typically the last provider whose
key they saved through the Keys tab).

User-visible symptoms:
  1. "Chat doesn't work — I get an error, maybe I'm out of credits."
     → because chat is using a stale provider whose key has issues, not
     the one the user just selected.
  2. "The API selector is bugged and weird."
     → the dropdown saves successfully but doesn't actually change which
     provider chat routes through.

Fix: `api_save_settings` calls `_write_preferred_provider_to_config_toml`
when body contains `preferred_provider`, in local mode only.
"""
from __future__ import annotations

import inspect
import re

import pytest


def test_helper_exists_at_module_level():
    """The shared helper must exist at module level so both
    `/api/setup/key` and `/api/settings` can call it."""
    import server
    assert hasattr(server, "_write_preferred_provider_to_config_toml"), (
        "BRAIN-PROD-2 regression: helper must exist so the Engine "
        "settings save and the Keys-tab save both write to the same "
        "config.toml line that get_provider() reads."
    )
    assert callable(server._write_preferred_provider_to_config_toml)


def test_helper_writes_preferred_provider_line(local_env, tmp_path, monkeypatch):
    """The helper writes a valid `preferred_provider = "<slug>"` line
    that `providers._load_local_settings()` parses successfully."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    import server
    ok, info = server._write_preferred_provider_to_config_toml("openai")
    assert ok, f"helper failed unexpectedly: {info}"
    cfg = tmp_path / "cfg" / "huntova" / "config.toml"
    assert cfg.exists(), "config.toml not created"
    text = cfg.read_text()
    assert 'preferred_provider = "openai"' in text, (
        f"expected preferred_provider line, got:\n{text}"
    )
    # Verify providers module can parse what we just wrote.
    import importlib, providers
    importlib.reload(providers)
    settings = providers._load_local_settings()
    assert settings.get("preferred_provider") == "openai", (
        "providers._load_local_settings() couldn't parse our write — "
        "the resolver won't honor the user's pick"
    )


def test_helper_replaces_existing_line(local_env, tmp_path, monkeypatch):
    """Switching providers must REPLACE the prior line, not append a
    second one — TOML with two `preferred_provider` keys raises on parse
    and the resolver silently falls back."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    import server
    server._write_preferred_provider_to_config_toml("anthropic")
    server._write_preferred_provider_to_config_toml("gemini")
    cfg = tmp_path / "cfg" / "huntova" / "config.toml"
    text = cfg.read_text()
    occurrences = re.findall(r"^preferred_provider\s*=", text, re.MULTILINE)
    assert len(occurrences) == 1, (
        f"helper must replace, not append. Found {len(occurrences)} "
        f"preferred_provider lines:\n{text}"
    )
    assert 'preferred_provider = "gemini"' in text


def test_helper_clears_line_on_empty_slug(local_env, tmp_path, monkeypatch):
    """Empty/whitespace slug must REMOVE the line so users can return
    to 'Auto' (first available key) — leaving a stale line would pin
    them to a possibly-broken provider."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    import server
    server._write_preferred_provider_to_config_toml("anthropic")
    server._write_preferred_provider_to_config_toml("")
    cfg = tmp_path / "cfg" / "huntova" / "config.toml"
    text = cfg.read_text() if cfg.exists() else ""
    assert "preferred_provider" not in text, (
        "empty slug must clear the line, not preserve it. "
        f"Got:\n{text}"
    )


def test_helper_preserves_other_keys(local_env, tmp_path, monkeypatch):
    """Existing HV_*_KEY entries (or other config.toml lines) must not
    be clobbered by the rewrite — the helper only touches
    `preferred_provider`."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    cfg_dir = tmp_path / "cfg" / "huntova"
    cfg_dir.mkdir(parents=True)
    cfg = cfg_dir / "config.toml"
    cfg.write_text(
        'preferred_provider = "anthropic"\n'
        'HV_ANTHROPIC_KEY = "sk-ant-test"\n'
        'HV_OPENAI_KEY = "sk-openai-test"\n'
    )
    import server
    server._write_preferred_provider_to_config_toml("openai")
    text = cfg.read_text()
    assert 'HV_ANTHROPIC_KEY = "sk-ant-test"' in text, "clobbered Anthropic key"
    assert 'HV_OPENAI_KEY = "sk-openai-test"' in text, "clobbered OpenAI key"
    assert 'preferred_provider = "openai"' in text, "missing new pick"
    assert 'preferred_provider = "anthropic"' not in text, "stale pick survived"


def test_api_save_settings_calls_config_toml_mirror():
    """Source-level guard: `api_save_settings` must call the helper
    when `preferred_provider` is in the body. Without this call, the
    DB write succeeds but `get_provider()` keeps reading the stale
    config.toml value — exact symptom of BRAIN-PROD-2."""
    from server import api_save_settings
    src = inspect.getsource(api_save_settings)
    assert '"preferred_provider" in body' in src or "'preferred_provider' in body" in src, (
        "BRAIN-PROD-2 regression: api_save_settings must check for "
        "preferred_provider in body and mirror to config.toml. "
        "Without the check, the chat dispatcher's get_provider() "
        "ignores the user's Engine selection."
    )
    assert "_write_preferred_provider_to_config_toml" in src, (
        "BRAIN-PROD-2 regression: api_save_settings must call the "
        "shared helper that writes config.toml — that's the only "
        "surface providers._load_local_settings() reads in local mode."
    )


def test_api_setup_key_uses_shared_helper():
    """The Keys-tab save path must use the same helper so config.toml
    has one canonical writer. Two divergent writers would re-introduce
    the inconsistency between Engine tab and Keys tab."""
    from server import api_setup_key
    src = inspect.getsource(api_setup_key)
    assert "_write_preferred_provider_to_config_toml" in src, (
        "api_setup_key should reuse the shared helper to keep the "
        "config.toml write contract single-source."
    )
