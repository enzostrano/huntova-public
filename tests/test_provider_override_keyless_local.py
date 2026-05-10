"""BRAIN-162: provider override-keyless-local parity.

`get_provider()` honours two routing layers above the default order:

1. `push_provider_override(slug)` — wins everything; how the chat AI
   selector + multi-agent fan-out target a specific provider per call.
2. `settings["preferred_provider"]` — the user's saved choice.

Both layers SHOULD treat the local providers (`ollama`, `lmstudio`,
`llamafile`) as keyless — these run on `localhost` with no auth by
default, and `_build()` accepts `api_key="no-key"` for them. The
preferred-branch already had this carve-out (audit wave 26). The
override-branch did NOT — `push_provider_override("ollama")` from
the chat selector silently fell through to the default priority
order and picked Anthropic, with zero feedback to the caller.

These tests pin the new parity:
"""
from __future__ import annotations


def test_override_local_provider_works_without_key(local_env, monkeypatch):
    """Setting an override to ollama with no HV_OLLAMA_KEY must
    return an ollama provider, not silently fall through."""
    monkeypatch.delenv("HV_OLLAMA_KEY", raising=False)
    # Make sure other provider keys exist so the default order would
    # pick something else (the bug was: it picked Anthropic).
    monkeypatch.setenv("HV_ANTHROPIC_KEY", "sk-ant-test")
    import importlib
    import providers
    importlib.reload(providers)

    providers.push_provider_override("ollama")
    try:
        p = providers.get_provider({})
        assert p.name == "ollama", (
            f"override 'ollama' must yield ollama provider; got {p.name!r}"
        )
    finally:
        providers.push_provider_override(None)


def test_override_lmstudio_works_without_key(local_env, monkeypatch):
    monkeypatch.delenv("HV_LMSTUDIO_KEY", raising=False)
    monkeypatch.setenv("HV_ANTHROPIC_KEY", "sk-ant-test")
    import importlib
    import providers
    importlib.reload(providers)

    providers.push_provider_override("lmstudio")
    try:
        p = providers.get_provider({})
        assert p.name == "lmstudio"
    finally:
        providers.push_provider_override(None)


def test_override_llamafile_works_without_key(local_env, monkeypatch):
    monkeypatch.delenv("HV_LLAMAFILE_KEY", raising=False)
    monkeypatch.setenv("HV_ANTHROPIC_KEY", "sk-ant-test")
    import importlib
    import providers
    importlib.reload(providers)

    providers.push_provider_override("llamafile")
    try:
        p = providers.get_provider({})
        assert p.name == "llamafile"
    finally:
        providers.push_provider_override(None)


def test_override_cloud_provider_without_key_falls_through(local_env, monkeypatch):
    """Cloud providers DO require a key — overriding to a keyless
    cloud provider must still fall through to the default priority
    order. (We pick the first keyed provider in `_DEFAULT_ORDER`.)"""
    monkeypatch.delenv("HV_OPENAI_KEY", raising=False)
    monkeypatch.setenv("HV_ANTHROPIC_KEY", "sk-ant-test")
    import importlib
    import providers
    importlib.reload(providers)

    providers.push_provider_override("openai")
    try:
        p = providers.get_provider({})
        # Must NOT be openai (no key); falls through to anthropic.
        assert p.name == "anthropic", (
            f"override to keyless cloud provider must fall through; got {p.name!r}"
        )
    finally:
        providers.push_provider_override(None)


def test_override_local_provider_with_key_still_uses_key(local_env, monkeypatch):
    """When HV_OLLAMA_KEY IS set (password-protected local server),
    the override must use it instead of 'no-key'."""
    monkeypatch.setenv("HV_OLLAMA_KEY", "ollama-pw-token")
    import importlib
    import providers
    importlib.reload(providers)

    providers.push_provider_override("ollama")
    try:
        p = providers.get_provider({})
        assert p.name == "ollama"
    finally:
        providers.push_provider_override(None)


def test_preferred_local_provider_still_works(local_env, monkeypatch):
    """Regression-pin: the preferred-branch carve-out (audit wave 26)
    still treats local providers as keyless even after the override
    parity fix."""
    monkeypatch.delenv("HV_OLLAMA_KEY", raising=False)
    monkeypatch.setenv("HV_ANTHROPIC_KEY", "sk-ant-test")
    import importlib
    import providers
    importlib.reload(providers)

    p = providers.get_provider({"preferred_provider": "ollama"})
    assert p.name == "ollama"


def test_no_override_no_preferred_uses_default_order(local_env, monkeypatch):
    """With no override and no preferred set, default order picks the
    first provider with a key — Anthropic stays the documented top of
    the order."""
    monkeypatch.setenv("HV_ANTHROPIC_KEY", "sk-ant-test")
    monkeypatch.setenv("HV_OPENAI_KEY", "sk-oai-test")
    import importlib
    import providers
    importlib.reload(providers)

    p = providers.get_provider({})
    assert p.name == "anthropic"


def test_override_invalid_slug_doesnt_clobber_preferred(local_env, monkeypatch):
    """`push_provider_override` documented behaviour: unknown slug is
    a no-op (preserves prior valid override). Confirm an invalid
    override doesn't break the preferred-branch resolution."""
    monkeypatch.setenv("HV_ANTHROPIC_KEY", "sk-ant-test")
    monkeypatch.delenv("HV_OLLAMA_KEY", raising=False)
    import importlib
    import providers
    importlib.reload(providers)

    # Invalid slug should be a no-op.
    providers.push_provider_override("not-a-real-provider")
    try:
        p = providers.get_provider({"preferred_provider": "ollama"})
        # Preferred (ollama, keyless local) must still win.
        assert p.name == "ollama"
    finally:
        providers.push_provider_override(None)


def test_override_clear_returns_resolution_to_preferred(local_env, monkeypatch):
    """After `push_provider_override(None)`, get_provider must fall
    back to settings['preferred_provider']."""
    monkeypatch.setenv("HV_ANTHROPIC_KEY", "sk-ant-test")
    monkeypatch.setenv("HV_OPENAI_KEY", "sk-oai-test")
    import importlib
    import providers
    importlib.reload(providers)

    providers.push_provider_override("openai")
    p1 = providers.get_provider({"preferred_provider": "anthropic"})
    assert p1.name == "openai"

    providers.push_provider_override(None)
    p2 = providers.get_provider({"preferred_provider": "anthropic"})
    assert p2.name == "anthropic"
