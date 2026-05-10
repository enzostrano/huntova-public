"""Runtime capability resolution."""
from __future__ import annotations


def test_local_mode_disables_billing(local_env):
    from runtime import CAPABILITIES
    assert CAPABILITIES.mode == "local"
    assert CAPABILITIES.billing_enabled is False
    assert CAPABILITIES.auth_enabled is False
    assert CAPABILITIES.single_user_mode is True
    assert CAPABILITIES.hosted_mode is False


def test_local_mode_capability_dict_round_trip(local_env):
    from runtime import CAPABILITIES
    d = CAPABILITIES.to_dict()
    for k in ("mode", "billing_enabled", "auth_enabled", "single_user_mode",
              "hosted_mode", "smtp_enabled", "public_share_enabled",
              "google_oauth_enabled"):
        assert k in d


def test_env_override_billing(local_env, monkeypatch):
    monkeypatch.setenv("HV_BILLING", "1")
    import importlib
    import runtime
    importlib.reload(runtime)
    assert runtime.CAPABILITIES.billing_enabled is True
