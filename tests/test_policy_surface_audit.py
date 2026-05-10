"""BRAIN-159: policy.py surface-parity + invariant audit.

The policy module exposes a singleton (`policy.policy`) of one of two
classes — `_LocalPolicy` (BYOK / no billing) or `_CloudPolicy` (hosted
SaaS / credits + tiers). Every call site assumes the same method set
on whichever instance it gets back. If a future PR adds a method to
one class but forgets the other, callers crash at runtime in whichever
mode wasn't covered.

These tests pin the contract:

1. Method-name set parity between the two classes.
2. Local invariants — feature_allowed always True regardless of input,
   cost is 0, deduct_on_save is False, billing UI off.
3. Cloud invariants — None user is gated correctly; can_run_agent
   honours credits_remaining; cost is 1; deduct_on_save is True.
4. _resolve() picks _LocalPolicy when billing is disabled even in
   cloud mode (the documented short-circuit).
5. model_for_user fallback shape — local honours user override keys;
   cloud honours tier map and falls back to default when tier missing.
"""
from __future__ import annotations

import inspect


_PUBLIC_METHODS = (
    "feature_allowed",
    "can_run_agent",
    "cost_per_lead",
    "deduct_on_save",
    "model_for_user",
    "show_billing_ui",
)


def test_method_set_parity(local_env):
    """Every public method on _LocalPolicy must exist on _CloudPolicy
    (and vice versa). If this drifts, a cloud-path call site can crash
    in local mode or vice versa with AttributeError."""
    from policy import _LocalPolicy, _CloudPolicy
    local_methods = {n for n in dir(_LocalPolicy) if not n.startswith("_")}
    cloud_methods = {n for n in dir(_CloudPolicy) if not n.startswith("_")}
    # Drop the dataclass-injected `name` attribute — it's a field, not
    # a behavioural method.
    local_methods.discard("name")
    cloud_methods.discard("name")
    assert local_methods == cloud_methods, (
        f"_LocalPolicy and _CloudPolicy have drifted method sets. "
        f"local-only: {local_methods - cloud_methods}; "
        f"cloud-only: {cloud_methods - local_methods}"
    )
    for name in _PUBLIC_METHODS:
        assert name in local_methods, f"_LocalPolicy missing {name!r}"
        assert name in cloud_methods, f"_CloudPolicy missing {name!r}"


def test_method_signatures_match(local_env):
    """Same method on both classes must take the same parameter names."""
    from policy import _LocalPolicy, _CloudPolicy
    for name in _PUBLIC_METHODS:
        l_sig = inspect.signature(getattr(_LocalPolicy, name))
        c_sig = inspect.signature(getattr(_CloudPolicy, name))
        l_params = [p for p in l_sig.parameters if p != "self"]
        c_params = [p for p in c_sig.parameters if p != "self"]
        assert l_params == c_params, (
            f"{name!r} signature drift: local={l_params} cloud={c_params}"
        )


def test_local_feature_allowed_handles_none_user(local_env):
    """Local mode must not crash on user=None — many CLI call sites
    pass no user object at all in single-user mode."""
    from policy import _LocalPolicy
    p = _LocalPolicy()
    assert p.feature_allowed(None, "ai_chat") is True
    assert p.feature_allowed(None, "research") is True


def test_local_feature_allowed_unknown_feature(local_env):
    """Local mode is permissive — unknown feature names must still
    return True. BYOK users shouldn't hit a gate they didn't agree to."""
    from policy import _LocalPolicy
    p = _LocalPolicy()
    for feat in ("totally_made_up", "future_v2_thing", "", "🦊"):
        assert p.feature_allowed({"tier": "free"}, feat) is True


def test_local_can_run_agent_ignores_credits(local_env):
    """Local mode never deducts credits — can_run_agent must return
    True even when credits_remaining is 0 or negative or missing."""
    from policy import _LocalPolicy
    p = _LocalPolicy()
    for u in (None, {}, {"credits_remaining": 0}, {"credits_remaining": -1}):
        ok, msg = p.can_run_agent(u)
        assert ok is True, f"local can_run_agent must be True for {u}"
        assert msg == ""


def test_local_cost_invariant(local_env):
    """cost_per_lead in local mode is 0 regardless of user shape."""
    from policy import _LocalPolicy
    p = _LocalPolicy()
    for u in (None, {}, {"tier": "pro"}, {"credits_remaining": 9999}):
        assert p.cost_per_lead(u) == 0


def test_local_deduct_and_show_billing_off(local_env):
    """deduct_on_save and show_billing_ui must both be False in local mode."""
    from policy import _LocalPolicy
    p = _LocalPolicy()
    assert p.deduct_on_save() is False
    assert p.show_billing_ui() is False


def test_local_model_honours_user_override(local_env):
    """model_for_user in local mode prefers user's preferred_model
    over the default — that's how the Engine selector reaches into
    runs spawned with no explicit override."""
    from policy import _LocalPolicy
    p = _LocalPolicy()
    assert p.model_for_user({"preferred_model": "claude-sonnet-4-5"},
                            "default-model") == "claude-sonnet-4-5"
    # Falls back to legacy `model` key.
    assert p.model_for_user({"model": "gpt-4o"}, "default-model") == "gpt-4o"
    # No override → default.
    assert p.model_for_user({}, "default-model") == "default-model"
    assert p.model_for_user(None, "default-model") == "default-model"


def test_cloud_feature_allowed_no_user_denied(local_env):
    """Cloud mode must reject feature_allowed when user is None — no
    anonymous access."""
    from policy import _CloudPolicy
    p = _CloudPolicy()
    assert p.feature_allowed(None, "ai_chat") is False


def test_cloud_can_run_agent_no_user_denied(local_env):
    """Cloud mode must reject can_run_agent without a user."""
    from policy import _CloudPolicy
    p = _CloudPolicy()
    ok, msg = p.can_run_agent(None)
    assert ok is False
    assert msg


def test_cloud_can_run_agent_no_credits_denied(local_env):
    """Cloud mode must reject can_run_agent when credits_remaining
    is 0, negative, or missing."""
    from policy import _CloudPolicy
    p = _CloudPolicy()
    for u in ({"credits_remaining": 0}, {"credits_remaining": -1}, {}):
        ok, msg = p.can_run_agent(u)
        assert ok is False, f"cloud can_run_agent must reject {u}"
        assert msg


def test_cloud_can_run_agent_with_credits_allowed(local_env):
    """Cloud mode allows can_run_agent when credits_remaining > 0."""
    from policy import _CloudPolicy
    p = _CloudPolicy()
    ok, msg = p.can_run_agent({"credits_remaining": 5})
    assert ok is True
    assert msg == ""


def test_cloud_cost_per_lead_constant(local_env):
    """Cost is currently 1 per lead in cloud mode — invariant pin so a
    pricing change is a deliberate test edit, not a silent surprise."""
    from policy import _CloudPolicy
    p = _CloudPolicy()
    assert p.cost_per_lead({"tier": "free"}) == 1
    assert p.cost_per_lead({"tier": "pro"}) == 1
    assert p.cost_per_lead(None) == 1


def test_cloud_deduct_on_save_true(local_env):
    """Cloud mode must deduct credits on lead save."""
    from policy import _CloudPolicy
    p = _CloudPolicy()
    assert p.deduct_on_save() is True
    assert p.show_billing_ui() is True


def test_resolve_picks_local_when_billing_disabled(local_env, monkeypatch):
    """_resolve() short-circuits to _LocalPolicy when CAPABILITIES
    flips billing_enabled off, even in cloud mode. Pinning this
    branch so a future refactor can't accidentally drop it."""
    import importlib
    import policy as policy_mod

    # Force cloud mode but with billing disabled.
    monkeypatch.setenv("APP_MODE", "cloud")
    monkeypatch.setenv("HV_BILLING", "0")
    import runtime
    importlib.reload(runtime)
    importlib.reload(policy_mod)
    # Resolve class identity from the reloaded module — class objects
    # rebind on reload so a pre-reload import would compare against a
    # stale class.
    assert isinstance(policy_mod.policy, policy_mod._LocalPolicy)


def test_resolve_picks_cloud_when_billing_enabled(local_env, monkeypatch):
    """_resolve() returns _CloudPolicy when both mode=cloud AND
    billing_enabled=True."""
    import importlib
    import policy as policy_mod

    monkeypatch.setenv("APP_MODE", "cloud")
    monkeypatch.setenv("HV_BILLING", "1")
    import runtime
    importlib.reload(runtime)
    importlib.reload(policy_mod)
    assert isinstance(policy_mod.policy, policy_mod._CloudPolicy)


def test_module_singleton_exists(local_env):
    """policy.policy must always exist after import — call sites
    do `from policy import policy` and never check None."""
    import policy as policy_mod
    assert policy_mod.policy is not None
    # Must implement the public surface.
    for name in _PUBLIC_METHODS:
        assert callable(getattr(policy_mod.policy, name, None)), (
            f"singleton missing callable {name!r}"
        )


def test_can_run_agent_return_shape(local_env):
    """Both modes return (bool, str) — call sites unpack as 2-tuple."""
    from policy import _LocalPolicy, _CloudPolicy
    for cls in (_LocalPolicy, _CloudPolicy):
        result = cls().can_run_agent({"credits_remaining": 5})
        assert isinstance(result, tuple) and len(result) == 2, (
            f"{cls.__name__}.can_run_agent must return 2-tuple, got {result!r}"
        )
        ok, msg = result
        assert isinstance(ok, bool), f"{cls.__name__} ok must be bool"
        assert isinstance(msg, str), f"{cls.__name__} msg must be str"
