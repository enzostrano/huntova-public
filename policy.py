"""
Billing + feature policy for Huntova (Phase 4 of the local-CLI pivot).

Single source of truth for "is this user allowed to do X right now?".
Every credit / tier / Stripe gate in the codebase consults policy here
instead of hard-coding the cloud rules. Local CLI mode short-circuits
to "always yes, no cost" without touching the call site.

Resolution flow:
    cloud  → existing tier + credits logic
    local  → unlimited everything (the user pays their own provider)

Usage:
    from policy import policy
    if policy.feature_allowed(user, "ai_chat"): ...
    if policy.can_run_agent(user): ...
    cost = policy.cost_per_lead(user)  # 0 in local mode
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from runtime import CAPABILITIES


@dataclass
class _LocalPolicy:
    """Single-user CLI: no billing, no tiers, no gates."""
    name: str = "local"

    def feature_allowed(self, user: dict | None, feature: str) -> bool:
        return True

    def can_run_agent(self, user: dict | None) -> tuple[bool, str]:
        return True, ""

    def cost_per_lead(self, user: dict | None) -> int:
        return 0

    def deduct_on_save(self) -> bool:
        return False

    def model_for_user(self, user: dict | None, default: str) -> str:
        # Local mode picks the user's preferred provider/model via
        # providers.get_provider() — config.MODEL_ID is just the
        # fallback. Honor any per-user override if present.
        if user:
            override = user.get("preferred_model") or user.get("model")
            if override:
                return str(override)
        return default

    def show_billing_ui(self) -> bool:
        return False


@dataclass
class _CloudPolicy:
    """Hosted SaaS: existing tier + credits behaviour."""
    name: str = "cloud"

    def feature_allowed(self, user: dict | None, feature: str) -> bool:
        if not user:
            return False
        # Mirrors auth.user_features. Single source so future
        # gate-list changes don't drift between two files.
        from auth import _feature_allowed_for_tier  # type: ignore[attr-defined]
        return _feature_allowed_for_tier(user.get("tier") or "free", feature)

    def can_run_agent(self, user: dict | None) -> tuple[bool, str]:
        if not user:
            return False, "not signed in"
        if int(user.get("credits_remaining") or 0) <= 0:
            return False, "no credits remaining"
        return True, ""

    def cost_per_lead(self, user: dict | None) -> int:
        return 1  # constant in current pricing; tier doesn't change cost

    def deduct_on_save(self) -> bool:
        return True

    def model_for_user(self, user: dict | None, default: str) -> str:
        if not user:
            return default
        from config import TIER_MODELS  # local import to avoid cycle at top
        tier = user.get("tier") or "free"
        return TIER_MODELS.get(tier, default)

    def show_billing_ui(self) -> bool:
        return True


def _resolve():
    if CAPABILITIES.mode == "local" or not CAPABILITIES.billing_enabled:
        return _LocalPolicy()
    return _CloudPolicy()


# Module-level singleton. Cheap to import.
policy = _resolve()
