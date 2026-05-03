"""
Huntova runtime capabilities.

The single source of truth for "what does this install of Huntova do?"
Two modes:

    cloud  — hosted SaaS as it exists today: PostgreSQL, Stripe, OAuth,
             SMTP, multi-user. Default in production.
    local  — single-user, downloaded CLI: SQLite, BYOK provider keys,
             no billing, no email verification gate.

Capability flags below are read by both backend (auth gates, agent
runner, payment routes) and frontend (which UI surfaces to render).
The flags are deliberately granular so the rip-list can flip surfaces
one at a time without touching every call site each round.

Set APP_MODE=local to turn the running install into the CLI shape.
Anything else (or unset) keeps the current cloud behaviour intact.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class RuntimeCapabilities:
    """Boolean capability flags resolved once at startup."""
    mode: str                  # "cloud" or "local"
    billing_enabled: bool      # show pricing modal, credit pill, Stripe routes
    auth_enabled: bool         # require login (cookie session). False in local mode.
    single_user_mode: bool     # skip per-user isolation; one user owns everything
    hosted_mode: bool          # absolute-URL email links, OAuth, etc.
    smtp_enabled: bool         # transactional email (verification, reset, weekly)
    public_share_enabled: bool # /h/<slug> public pages
    google_oauth_enabled: bool # /auth/google* routes

    def to_dict(self) -> dict:
        return asdict(self)


def _truthy(v: str | None, default: bool) -> bool:
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _resolve() -> RuntimeCapabilities:
    """Read env vars once. Local mode overrides everything to off-by-default."""
    mode = (os.environ.get("APP_MODE") or "cloud").strip().lower()
    if mode not in ("cloud", "local"):
        mode = "cloud"

    if mode == "local":
        # Local CLI defaults: nothing remote, no billing, single user.
        # Each flag still readable from env for advanced users who run
        # the CLI in a custom shape (e.g. local + smtp for email tests).
        return RuntimeCapabilities(
            mode="local",
            billing_enabled=_truthy(os.environ.get("HV_BILLING"), False),
            auth_enabled=_truthy(os.environ.get("HV_AUTH"), False),
            single_user_mode=_truthy(os.environ.get("HV_SINGLE_USER"), True),
            hosted_mode=False,
            smtp_enabled=_truthy(os.environ.get("HV_SMTP"), False),
            public_share_enabled=_truthy(os.environ.get("HV_PUBLIC_SHARE"), True),
            google_oauth_enabled=_truthy(os.environ.get("HV_GOOGLE_OAUTH"), False),
        )

    # Cloud: keep current behaviour unchanged. Each flag still respects
    # an explicit override so a deploy can opt out of any surface.
    # Stability fix (audit wave 29): google_oauth_enabled was the one
    # cloud flag that bypassed the `HV_*` override pattern — it read
    # bool(GOOGLE_CLIENT_ID) directly with no way for ops to disable
    # OAuth via env when the client_id was set, or force-enable it
    # for a staging deploy without setting the client_id. Match the
    # other 7 flags: HV_GOOGLE_OAUTH overrides; default tracks the
    # presence of GOOGLE_CLIENT_ID.
    return RuntimeCapabilities(
        mode="cloud",
        billing_enabled=_truthy(os.environ.get("HV_BILLING"), True),
        auth_enabled=_truthy(os.environ.get("HV_AUTH"), True),
        single_user_mode=_truthy(os.environ.get("HV_SINGLE_USER"), False),
        hosted_mode=_truthy(os.environ.get("HV_HOSTED"), True),
        smtp_enabled=_truthy(os.environ.get("HV_SMTP"), True),
        public_share_enabled=_truthy(os.environ.get("HV_PUBLIC_SHARE"), True),
        google_oauth_enabled=_truthy(os.environ.get("HV_GOOGLE_OAUTH"),
                                     bool(os.environ.get("GOOGLE_CLIENT_ID"))),
    )


CAPABILITIES: RuntimeCapabilities = _resolve()


def get_capabilities() -> RuntimeCapabilities:
    """a292: prefer this over the module-level CAPABILITIES global. The
    `frozen=True` dataclass blocks `CAPABILITIES.billing_enabled = X`
    but does NOT block `runtime.CAPABILITIES = something_else` from
    any caller — the rebind only affects callers who do
    `runtime.CAPABILITIES.foo`, but anyone with `from runtime import
    CAPABILITIES` keeps the original. This footgun was flagged by
    the round 1 cycle 2 audit. Use `get_capabilities()` instead — it
    always returns the live singleton."""
    return CAPABILITIES


def is_local() -> bool:
    return CAPABILITIES.mode == "local"


def is_cloud() -> bool:
    return CAPABILITIES.mode == "cloud"
