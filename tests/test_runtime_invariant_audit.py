"""BRAIN-160: runtime.py invariant audit.

`runtime.CAPABILITIES` is the single source of truth for "what does
this install do?" — every backend gate and every frontend feature
flag reads it. The dataclass is frozen so individual field mutations
raise. The resolver is run once at import.

These tests pin:

1. `_truthy` accepts case-insensitive variants of the truthy strings
   and rejects everything else.
2. `_resolve` defaults to cloud mode for unknown / blank APP_MODE.
3. Local-mode default flag values are correct (off-by-default).
4. Cloud-mode default flag values are correct (on-by-default).
5. `frozen=True` blocks per-field mutation.
6. `to_dict()` returns all 8 documented fields.
7. `get_capabilities()` returns the live module singleton.
8. `is_local()` / `is_cloud()` agree with `CAPABILITIES.mode`.
9. `google_oauth_enabled` cloud default tracks `GOOGLE_CLIENT_ID`
   presence (the audit-wave-29 fix).
10. Whitespace-padded `APP_MODE` resolves correctly.
"""
from __future__ import annotations

import importlib
import pytest


def _reload_runtime():
    import runtime
    importlib.reload(runtime)
    return runtime


def test_truthy_case_insensitive(local_env, monkeypatch):
    from runtime import _truthy
    for v in ("1", "true", "TRUE", "True", "yes", "YES", "on", "ON"):
        assert _truthy(v, default=False) is True, f"{v!r} should be truthy"
    for v in ("0", "false", "FALSE", "no", "off", "", "anything", "  "):
        assert _truthy(v, default=True) is False, f"{v!r} should be falsy"


def test_truthy_default_when_none(local_env):
    from runtime import _truthy
    assert _truthy(None, default=True) is True
    assert _truthy(None, default=False) is False


def test_truthy_handles_whitespace_padding(local_env):
    from runtime import _truthy
    assert _truthy("  1  ", default=False) is True
    assert _truthy("\ttrue\n", default=False) is True


def test_resolve_defaults_to_cloud_for_unknown_mode(local_env, monkeypatch):
    monkeypatch.setenv("APP_MODE", "weird-unknown")
    rt = _reload_runtime()
    assert rt.CAPABILITIES.mode == "cloud"


def test_resolve_defaults_to_cloud_for_blank(local_env, monkeypatch):
    monkeypatch.delenv("APP_MODE", raising=False)
    rt = _reload_runtime()
    assert rt.CAPABILITIES.mode == "cloud"


def test_resolve_lowercases_mode(local_env, monkeypatch):
    monkeypatch.setenv("APP_MODE", "LOCAL")
    rt = _reload_runtime()
    assert rt.CAPABILITIES.mode == "local"


def test_resolve_strips_whitespace_from_mode(local_env, monkeypatch):
    monkeypatch.setenv("APP_MODE", "  local  ")
    rt = _reload_runtime()
    assert rt.CAPABILITIES.mode == "local"


def test_local_mode_defaults(local_env, monkeypatch):
    """Local-mode flag defaults — off-by-default for everything except
    single_user_mode + public_share_enabled."""
    monkeypatch.setenv("APP_MODE", "local")
    for k in ("HV_BILLING", "HV_AUTH", "HV_SINGLE_USER", "HV_SMTP",
             "HV_PUBLIC_SHARE", "HV_GOOGLE_OAUTH"):
        monkeypatch.delenv(k, raising=False)
    rt = _reload_runtime()
    c = rt.CAPABILITIES
    assert c.mode == "local"
    assert c.billing_enabled is False
    assert c.auth_enabled is False
    assert c.single_user_mode is True
    assert c.hosted_mode is False
    assert c.smtp_enabled is False
    assert c.public_share_enabled is True
    assert c.google_oauth_enabled is False


def test_cloud_mode_defaults(local_env, monkeypatch):
    """Cloud-mode flag defaults — on-by-default."""
    monkeypatch.setenv("APP_MODE", "cloud")
    for k in ("HV_BILLING", "HV_AUTH", "HV_SINGLE_USER", "HV_HOSTED",
             "HV_SMTP", "HV_PUBLIC_SHARE", "HV_GOOGLE_OAUTH",
             "GOOGLE_CLIENT_ID"):
        monkeypatch.delenv(k, raising=False)
    rt = _reload_runtime()
    c = rt.CAPABILITIES
    assert c.mode == "cloud"
    assert c.billing_enabled is True
    assert c.auth_enabled is True
    assert c.single_user_mode is False
    assert c.hosted_mode is True
    assert c.smtp_enabled is True
    assert c.public_share_enabled is True
    # GOOGLE_CLIENT_ID is unset → google_oauth_enabled defaults False.
    assert c.google_oauth_enabled is False


def test_google_oauth_tracks_client_id_presence(local_env, monkeypatch):
    """Audit-wave-29 fix: cloud google_oauth_enabled defaults to
    bool(GOOGLE_CLIENT_ID) but HV_GOOGLE_OAUTH overrides."""
    monkeypatch.setenv("APP_MODE", "cloud")
    monkeypatch.delenv("HV_GOOGLE_OAUTH", raising=False)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "abc123")
    rt = _reload_runtime()
    assert rt.CAPABILITIES.google_oauth_enabled is True

    # Now override to disable it.
    monkeypatch.setenv("HV_GOOGLE_OAUTH", "0")
    rt = _reload_runtime()
    assert rt.CAPABILITIES.google_oauth_enabled is False

    # And without GOOGLE_CLIENT_ID, can force-enable via HV_GOOGLE_OAUTH.
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.setenv("HV_GOOGLE_OAUTH", "1")
    rt = _reload_runtime()
    assert rt.CAPABILITIES.google_oauth_enabled is True


def test_capabilities_frozen(local_env):
    """frozen=True must block per-field mutation."""
    from runtime import CAPABILITIES
    with pytest.raises((AttributeError, Exception)):
        CAPABILITIES.billing_enabled = True  # type: ignore[misc]


def test_to_dict_has_all_fields(local_env):
    """to_dict() must round-trip every documented capability flag."""
    from runtime import CAPABILITIES
    d = CAPABILITIES.to_dict()
    expected = {
        "mode", "billing_enabled", "auth_enabled", "single_user_mode",
        "hosted_mode", "smtp_enabled", "public_share_enabled",
        "google_oauth_enabled",
    }
    assert set(d.keys()) == expected, (
        f"to_dict() drift — expected {expected}, got {set(d.keys())}"
    )


def test_get_capabilities_returns_live_singleton(local_env):
    """get_capabilities() must return the module's CAPABILITIES, not
    a copy or a stale value."""
    import runtime
    assert runtime.get_capabilities() is runtime.CAPABILITIES


def test_is_local_is_cloud_agree_with_mode(local_env, monkeypatch):
    monkeypatch.setenv("APP_MODE", "local")
    rt = _reload_runtime()
    assert rt.is_local() is True
    assert rt.is_cloud() is False
    assert rt.is_local() != rt.is_cloud()


def test_is_local_is_cloud_in_cloud_mode(local_env, monkeypatch):
    monkeypatch.setenv("APP_MODE", "cloud")
    rt = _reload_runtime()
    assert rt.is_local() is False
    assert rt.is_cloud() is True


def test_billing_disabled_via_env_in_cloud(local_env, monkeypatch):
    """HV_BILLING=0 in cloud mode disables billing — used by self-hosted
    cloud deploys that want SaaS surfaces but no Stripe integration."""
    monkeypatch.setenv("APP_MODE", "cloud")
    monkeypatch.setenv("HV_BILLING", "0")
    rt = _reload_runtime()
    assert rt.CAPABILITIES.mode == "cloud"
    assert rt.CAPABILITIES.billing_enabled is False


def test_local_billing_can_be_enabled(local_env, monkeypatch):
    """HV_BILLING=1 in local mode enables billing — for development
    against the cloud billing surface."""
    monkeypatch.setenv("APP_MODE", "local")
    monkeypatch.setenv("HV_BILLING", "1")
    rt = _reload_runtime()
    assert rt.CAPABILITIES.mode == "local"
    assert rt.CAPABILITIES.billing_enabled is True
