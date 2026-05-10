"""Provider abstraction — key resolution, default selection, error paths."""
from __future__ import annotations

import pytest


def test_get_provider_picks_anthropic_by_default(local_env, monkeypatch):
    # Anthropic is now the default when its key is present.
    monkeypatch.setenv("HV_ANTHROPIC_KEY", "test-anthropic-key")
    from providers import get_provider, AnthropicProvider
    p = get_provider()
    assert p.name == "anthropic"
    assert isinstance(p, AnthropicProvider)


def test_get_provider_picks_gemini_when_only_gemini_key_set(local_env):
    # Backwards-compat: if the user only configured HV_GEMINI_KEY,
    # default selection still resolves to gemini.
    from providers import get_provider, GeminiProvider
    p = get_provider()
    assert p.name == "gemini"
    assert isinstance(p, GeminiProvider)


def test_get_provider_respects_preferred_provider(local_env, monkeypatch):
    monkeypatch.setenv("HV_OPENAI_KEY", "test-openai-key")
    from providers import get_provider
    p = get_provider({"preferred_provider": "openai"})
    assert p.name == "openai"


def test_get_provider_falls_through_to_available(local_env, monkeypatch):
    # Preferred not set, anthropic absent, openai absent, gemini present
    from providers import get_provider
    p = get_provider({"preferred_provider": "anthropic"})
    # Anthropic key isn't set, so we fall back to first available — gemini.
    assert p.name == "gemini"


def test_get_provider_raises_when_no_keys(local_env, monkeypatch):
    monkeypatch.delenv("HV_GEMINI_KEY", raising=False)
    monkeypatch.delenv("HV_ANTHROPIC_KEY", raising=False)
    monkeypatch.delenv("HV_OPENAI_KEY", raising=False)
    # Stub secrets_store so it doesn't return a key from a real store.
    import providers
    monkeypatch.setattr(providers, "_key_for", lambda *a, **k: None)
    with pytest.raises(RuntimeError, match="no API key configured"):
        providers.get_provider({})


def test_list_available_providers(local_env, monkeypatch):
    from providers import list_available_providers
    available = list_available_providers()
    assert "gemini" in available
    assert "anthropic" not in available
    assert "openai" not in available


def test_chat_compat_returns_openai_shape(local_env, monkeypatch):
    """Drop-in shim should expose .choices[0].message.content."""
    import providers

    class _StubProvider:
        name = "stub"

        def chat(self, messages, model=None, max_tokens=2048,
                 temperature=0.7, timeout_s=30.0, response_format=None):
            return "hello world"

    monkeypatch.setattr(providers, "get_provider", lambda *a, **k: _StubProvider())
    resp = providers.chat_compat(messages=[{"role": "user", "content": "hi"}])
    assert resp.choices[0].message.content == "hello world"
    assert resp.choices[0].message.role == "assistant"
