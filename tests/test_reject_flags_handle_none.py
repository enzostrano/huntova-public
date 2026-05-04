"""Regression test for BRAIN-29 (a390): reject_* flags use
`.get(key, True)` to default to "reject by default". But
`.get(key, default)` returns None when value is explicitly None
(legacy / migration / corruption) — and None is falsy, silently
inverting the user's intent from "reject" to "allow".

Fix: explicit `is not False` check — only an EXPLICIT False
opts out; None/True/missing all reject (preserve the
strict-by-default semantics).
"""
from __future__ import annotations


def _fake_wiz(**flags):
    return {
        "services": ["consulting"],
        "buyer_roles": ["CEO"],
        "icp_industries": ["fintech"],
        **flags,
    }


def test_reject_enterprise_with_explicit_none_still_rejects():
    """If the wizard blob has reject_enterprise=None (legacy /
    migration), the brain should still build with the strict
    default, not silently invert to 'allow'."""
    from app import _build_hunt_brain
    brain = _build_hunt_brain(_fake_wiz(reject_enterprise=None))
    assert brain["buyability_rules"]["reject_enterprise"] is True or brain["buyability_rules"]["reject_enterprise"] is None, (
        "BRAIN-29: explicit None must NOT silently flip strict "
        "default to permissive — at minimum, store the value the "
        "user expects (None should be treated as 'use the default')."
    )
    # Stronger: the enterprise_tolerance should be "reject"
    # because the user never explicitly set False.
    assert brain["enterprise_tolerance"] == "reject", (
        f"BRAIN-29 regression: None should NOT be treated as opt-out. "
        f"Got enterprise_tolerance={brain['enterprise_tolerance']}"
    )


def test_reject_enterprise_explicit_false_opts_out():
    """Don't regress the explicit-opt-out path."""
    from app import _build_hunt_brain
    brain = _build_hunt_brain(_fake_wiz(reject_enterprise=False))
    assert brain["enterprise_tolerance"] == "allow"


def test_reject_enterprise_missing_uses_strict_default():
    """Missing key (most users) preserves strict default."""
    from app import _build_hunt_brain
    brain = _build_hunt_brain(_fake_wiz())
    assert brain["enterprise_tolerance"] == "reject"
