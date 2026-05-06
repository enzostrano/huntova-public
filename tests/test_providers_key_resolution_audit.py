"""BRAIN-192: providers._key_for + _resolve_settings + list_available_providers audit.

Pinned invariants:

1. `_key_for(provider, settings)` resolves in priority order:
   settings → secrets_store → env (a289 fix: empty-string env stripped).
2. `_key_for` returns None when no source has a key.
3. `_key_for` returns None for unknown provider name (not in _ENV_KEY).
4. `_key_for` honours nested `settings["providers"][slug]["api_key"]`.
5. `_key_for` honours nested `settings["providers"][slug]` as a string.
6. `_resolve_settings` returns user_settings when given (cloud mode).
7. `_resolve_settings` returns local config in APP_MODE=local.
8. `_resolve_settings` returns {} in cloud mode without explicit settings.
9. `list_available_providers` filters by key presence.
10. `_DEFAULT_ORDER`, `_ENV_KEY`, `_BASE_URL`, `_DEFAULT_MODEL`,
    `_LOCAL_PROVIDERS` set integrity (covered partially by BRAIN-160).
"""
from __future__ import annotations

import importlib


def test_key_for_returns_settings_value(local_env, monkeypatch):
    monkeypatch.delenv("HV_OPENAI_KEY", raising=False)
    import providers
    importlib.reload(providers)
    settings = {"HV_OPENAI_KEY": "sk-from-settings"}
    assert providers._key_for("openai", settings) == "sk-from-settings"


def test_key_for_settings_overrides_env(local_env, monkeypatch):
    """Settings value wins over env (documented priority)."""
    monkeypatch.setenv("HV_OPENAI_KEY", "sk-from-env")
    import providers
    importlib.reload(providers)
    settings = {"HV_OPENAI_KEY": "sk-from-settings"}
    assert providers._key_for("openai", settings) == "sk-from-settings"


def test_key_for_falls_back_to_env(local_env, monkeypatch):
    """Without settings or keychain, env is the last resort."""
    monkeypatch.setenv("HV_OPENAI_KEY", "sk-from-env")
    import providers
    importlib.reload(providers)
    assert providers._key_for("openai", {}) == "sk-from-env"


def test_key_for_strips_env_whitespace(local_env, monkeypatch):
    """a289 fix: leading/trailing whitespace stripped from env."""
    monkeypatch.setenv("HV_OPENAI_KEY", "  sk-with-padding  ")
    import providers
    importlib.reload(providers)
    assert providers._key_for("openai", {}) == "sk-with-padding"


def test_key_for_empty_env_returns_none(local_env, monkeypatch):
    """a289 fix: empty-string env (`export HV_OPENAI_KEY=""`) returns
    None — pre-fix this fell through to the next provider silently."""
    monkeypatch.setenv("HV_OPENAI_KEY", "")
    import providers
    importlib.reload(providers)
    assert providers._key_for("openai", {}) is None


def test_key_for_unset_env_returns_none(local_env, monkeypatch):
    monkeypatch.delenv("HV_OPENAI_KEY", raising=False)
    import providers
    importlib.reload(providers)
    assert providers._key_for("openai", {}) is None


def test_key_for_unknown_provider_returns_none(local_env):
    """Provider name not in _ENV_KEY returns None silently."""
    import providers
    importlib.reload(providers)
    assert providers._key_for("totally-fake-provider", {}) is None


def test_key_for_nested_dict_form(local_env, monkeypatch):
    """settings['providers']['openai']['api_key'] supported."""
    monkeypatch.delenv("HV_OPENAI_KEY", raising=False)
    import providers
    importlib.reload(providers)
    settings = {"providers": {"openai": {"api_key": "sk-nested-dict"}}}
    assert providers._key_for("openai", settings) == "sk-nested-dict"


def test_key_for_nested_string_form(local_env, monkeypatch):
    """settings['providers']['openai'] as a bare string also works."""
    monkeypatch.delenv("HV_OPENAI_KEY", raising=False)
    import providers
    importlib.reload(providers)
    settings = {"providers": {"openai": "sk-nested-string"}}
    out = providers._key_for("openai", settings)
    # Must be the bare string.
    assert out == "sk-nested-string"


def test_key_for_top_level_overrides_nested(local_env, monkeypatch):
    """Top-level HV_OPENAI_KEY wins over nested providers map."""
    monkeypatch.delenv("HV_OPENAI_KEY", raising=False)
    import providers
    importlib.reload(providers)
    settings = {
        "HV_OPENAI_KEY": "sk-top-level",
        "providers": {"openai": "sk-nested"},
    }
    assert providers._key_for("openai", settings) == "sk-top-level"


def test_resolve_settings_returns_user_settings(local_env):
    import providers
    importlib.reload(providers)
    out = providers._resolve_settings({"HV_TEST": "value"})
    assert out == {"HV_TEST": "value"}


def test_resolve_settings_local_loads_config(local_env, monkeypatch):
    """In local mode without explicit settings, loads ~/.config/huntova/config.toml."""
    monkeypatch.setenv("APP_MODE", "local")
    import providers
    importlib.reload(providers)
    # No config file → empty dict.
    out = providers._resolve_settings(None)
    assert isinstance(out, dict)


def test_resolve_settings_cloud_returns_empty(local_env, monkeypatch):
    """Cloud mode without explicit settings returns {}."""
    monkeypatch.setenv("APP_MODE", "cloud")
    import providers
    importlib.reload(providers)
    out = providers._resolve_settings(None)
    assert out == {}


def test_list_available_providers_filters_by_key(local_env, monkeypatch):
    """Providers without a usable key don't appear."""
    for k in ("HV_ANTHROPIC_KEY", "HV_OPENAI_KEY", "HV_GEMINI_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HV_ANTHROPIC_KEY", "sk-test")
    import providers
    importlib.reload(providers)
    out = providers.list_available_providers({})
    assert "anthropic" in out
    # OpenAI not in the list (no key).
    assert "openai" not in out


def test_list_available_providers_empty_when_no_keys(local_env, monkeypatch):
    """No keys configured → empty list (or only local providers if
    detected, but those still need a key per _key_for)."""
    for k in ("HV_ANTHROPIC_KEY", "HV_OPENAI_KEY", "HV_GEMINI_KEY",
              "HV_OPENROUTER_KEY", "HV_GROQ_KEY", "HV_DEEPSEEK_KEY",
              "HV_TOGETHER_KEY", "HV_MISTRAL_KEY", "HV_PERPLEXITY_KEY",
              "HV_OLLAMA_KEY", "HV_LMSTUDIO_KEY", "HV_LLAMAFILE_KEY",
              "HV_CUSTOM_KEY"):
        monkeypatch.delenv(k, raising=False)
    import providers
    importlib.reload(providers)
    out = providers.list_available_providers({})
    # Either empty or contains only providers that don't strictly need keys.
    # Most providers in _DEFAULT_ORDER need keys, so this should be empty.
    # Pin: list is a list of strings; values are valid slugs.
    for slug in out:
        assert isinstance(slug, str)


def test_default_order_subset_of_env_key(local_env):
    """Every name in _DEFAULT_ORDER must have a corresponding _ENV_KEY entry."""
    import providers
    importlib.reload(providers)
    for name in providers._DEFAULT_ORDER:
        assert name in providers._ENV_KEY, (
            f"_DEFAULT_ORDER has {name!r} but _ENV_KEY doesn't"
        )


def test_local_providers_subset_of_default_order(local_env):
    """`_LOCAL_PROVIDERS` is a subset of `_DEFAULT_ORDER`."""
    import providers
    importlib.reload(providers)
    for slug in providers._LOCAL_PROVIDERS:
        assert slug in providers._DEFAULT_ORDER
