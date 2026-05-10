"""BillingPolicy local vs cloud."""
from __future__ import annotations


def test_local_policy_unlocks_everything(local_env):
    from policy import policy
    assert policy.name == "local"
    assert policy.feature_allowed({"tier": "free"}, "ai_chat") is True
    assert policy.feature_allowed({"tier": "free"}, "research") is True
    assert policy.feature_allowed({"tier": "free"}, "export_json") is True
    # Unknown feature also allowed in local mode (BYOK = unrestricted).
    assert policy.feature_allowed({"tier": "free"}, "made_up_feature_xyz") is True


def test_local_policy_no_billing(local_env):
    from policy import policy
    assert policy.show_billing_ui() is False
    assert policy.cost_per_lead({"tier": "free"}) == 0
    assert policy.deduct_on_save() is False


def test_local_policy_can_run_agent(local_env):
    from policy import policy
    ok, msg = policy.can_run_agent({"id": 1, "credits_remaining": 0})
    assert ok is True
    assert msg == ""
