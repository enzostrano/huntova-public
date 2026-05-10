"""Test push_provider_override invalid-slug + clear-vs-typo distinction.

a289 P1 fixed: passing a typo (e.g. "claude" for "anthropic") used to
silently CLEAR the override, clobbering the prior valid value with no
feedback. Now: empty/None clears; invalid slug logs warning and is a
no-op so prior state survives.

a307 audit found this regression had no test. This file closes it.
"""


def test_push_provider_override_valid_slug_sets_state():
    from providers import push_provider_override, _provider_override
    push_provider_override(None)  # start clean
    push_provider_override("anthropic")
    assert _provider_override.get() == "anthropic"
    push_provider_override(None)  # cleanup


def test_push_provider_override_clears_on_none():
    from providers import push_provider_override, _provider_override
    push_provider_override("anthropic")
    push_provider_override(None)
    assert _provider_override.get() == ""


def test_push_provider_override_clears_on_empty_string():
    from providers import push_provider_override, _provider_override
    push_provider_override("anthropic")
    push_provider_override("")
    assert _provider_override.get() == ""


def test_push_provider_override_invalid_slug_preserves_prior_state(capfd):
    """A typo must NOT clobber the prior valid override."""
    from providers import push_provider_override, _provider_override
    push_provider_override("anthropic")
    assert _provider_override.get() == "anthropic"
    push_provider_override("claude")  # typo — invalid slug
    assert _provider_override.get() == "anthropic"  # unchanged
    # Verify a warning was emitted to stderr
    out, err = capfd.readouterr()
    assert "claude" in err.lower() or "claude" in out.lower()
    push_provider_override(None)  # cleanup


def test_key_for_empty_string_env_returns_none(monkeypatch):
    """Empty-string env var should be treated as missing, not silently
    fall through to the next provider in _DEFAULT_ORDER (a289 fix)."""
    from providers import _key_for
    monkeypatch.setenv("HV_GEMINI_KEY", "")
    monkeypatch.setenv("HV_OPENAI_KEY", "")
    monkeypatch.setenv("HV_ANTHROPIC_KEY", "")
    assert _key_for("gemini", {}) is None
    assert _key_for("openai", {}) is None
    assert _key_for("anthropic", {}) is None


def test_key_for_whitespace_only_env_returns_none(monkeypatch):
    """Whitespace-only env var also treated as missing — same fix."""
    from providers import _key_for
    monkeypatch.setenv("HV_GEMINI_KEY", "   \t\n  ")
    assert _key_for("gemini", {}) is None
