"""Regression tests for BRAIN-96 (a465): every wizard endpoint
that spends BYOK money must enforce a durable daily quota,
not just a per-minute rate bucket.

Failure mode (Per Huntova engineering review on cost-governance
parity):

BRAIN-91 (a460) gave each wizard endpoint a per-minute bucket.
BRAIN-92/93/94 (a461-a463) added a durable daily quota for
`/api/wizard/scan`. The other three paid endpoints stayed
behind:

- `/api/wizard/generate-phase5` — fires Gemini Pro / configured
  provider on a multi-thousand-token prompt to generate 5
  follow-up questions. ~$0.02 / call. 8/min × 60 × 24 =
  11,520 calls/day → ~$230/day max.
- `/api/wizard/complete` — runs the synchronous brain +
  dossier compute (~5-30s), kicks off background DNA
  generation. ~$0.10 / call all-in. 6/min × 60 × 24 = 8,640
  calls/day → ~$864/day max (mitigated by BRAIN-85
  idempotency cache, but cache-misses still spend).
- `/api/wizard/assist` — runs an AI chat turn on the user's
  configured provider. ~$0.01 / call. 30/min × 60 × 24 =
  43,200 calls/day → ~$432/day max.

Each endpoint follows the same slow-burn pattern: stay under
the per-minute cap, drain the BYOK wallet over hours.

Standard cost-governance guidance: per-route quotas in
addition to per-route rate limits. Quotas cap long-horizon
spend; rate limits cap burst speed.

Invariants:
- New `_check_paid_endpoint_quota_async(user_id, bucket_name,
  daily_max)` (or per-endpoint helpers) enforces a durable
  daily counter under
  `_quotas.<bucket_name> = {date, count}`.
- Counter lives at the SETTINGS ROOT (parity with BRAIN-93
  scan quota — survives wizard reset).
- Each of `phase5`, `complete`, `assist` has its own cap
  exposed as a constant; cap is env-overridable.
- Quota check runs BEFORE any AI call so denials are cheap.
- Distinct error_kind per endpoint so the UI can show
  specific recovery messages.
"""
from __future__ import annotations
import inspect
import asyncio


def test_phase5_quota_constant_exists():
    """Source-level: a per-endpoint daily cap constant must
    exist for phase-5 generation."""
    import server as _s
    cap = getattr(_s, "_PHASE5_DAILY_MAX", None)
    assert cap is not None, (
        "BRAIN-96 regression: `_PHASE5_DAILY_MAX` constant "
        "must be exposed on server."
    )
    assert isinstance(cap, int) and 5 <= cap <= 500, (
        f"BRAIN-96 regression: phase5 cap {cap} unreasonable. "
        f"Should be 10-100. Below 5 frustrates demo users; "
        f"above 500 lets slow-burn drain $10+/day."
    )


def test_complete_quota_constant_exists():
    """Source-level: complete endpoint has its own cap."""
    import server as _s
    cap = getattr(_s, "_COMPLETE_DAILY_MAX", None)
    assert cap is not None, (
        "BRAIN-96 regression: `_COMPLETE_DAILY_MAX` must "
        "exist."
    )
    assert isinstance(cap, int) and 5 <= cap <= 200


def test_assist_quota_constant_exists():
    """Source-level: assist has its own cap. More generous
    than scan (chat is bursty by nature) but still bounded."""
    import server as _s
    cap = getattr(_s, "_ASSIST_DAILY_MAX", None)
    assert cap is not None, (
        "BRAIN-96 regression: `_ASSIST_DAILY_MAX` must exist."
    )
    assert isinstance(cap, int) and 20 <= cap <= 1000


def test_phase5_endpoint_enforces_daily_quota():
    """Source-level: api_wizard_generate_phase5 must check a
    daily quota in addition to the BRAIN-91 bucket."""
    from server import api_wizard_generate_phase5
    src = inspect.getsource(api_wizard_generate_phase5)
    assert (
        "_PHASE5_DAILY_MAX" in src
        or "_check_paid_endpoint_quota" in src
        or "phase5_daily" in src.lower()
    ), (
        "BRAIN-96 regression: generate-phase5 must enforce a "
        "daily quota."
    )


def test_complete_endpoint_enforces_daily_quota():
    """Source-level: api_wizard_complete must check a daily
    quota. Cache-miss completes still spend BYOK money."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    assert (
        "_COMPLETE_DAILY_MAX" in src
        or "_check_paid_endpoint_quota" in src
        or "complete_daily" in src.lower()
    ), (
        "BRAIN-96 regression: complete must enforce a daily "
        "quota."
    )


def test_assist_endpoint_enforces_daily_quota():
    """Source-level: api_wizard_assist must check a daily
    quota."""
    from server import api_wizard_assist
    src = inspect.getsource(api_wizard_assist)
    assert (
        "_ASSIST_DAILY_MAX" in src
        or "_check_paid_endpoint_quota" in src
        or "assist_daily" in src.lower()
    ), (
        "BRAIN-96 regression: assist must enforce a daily "
        "quota."
    )


def test_paid_quota_helper_uses_merge_settings():
    """Source-level: the durable quota helper must use
    db.merge_settings (atomic + survives restarts), parity
    with BRAIN-93's scan quota."""
    import server as _s
    helper = getattr(_s, "_check_paid_endpoint_quota_async", None) or \
             getattr(_s, "_check_phase5_daily_quota_async", None)
    assert helper is not None, (
        "BRAIN-96 regression: an async durable-quota helper "
        "for the paid endpoints must exist on server."
    )
    src = inspect.getsource(helper)
    assert "merge_settings" in src, (
        "BRAIN-96 regression: paid-endpoint quota helper must "
        "use db.merge_settings for atomicity + durability."
    )


def test_paid_quota_helper_lives_at_settings_root():
    """Source-level: counter must live OUTSIDE the wizard
    sub-object so a wizard reset doesn't refund the daily
    cap (parity with BRAIN-93)."""
    import server as _s
    helper = getattr(_s, "_check_paid_endpoint_quota_async", None) or \
             getattr(_s, "_check_phase5_daily_quota_async", None)
    src = inspect.getsource(helper)
    assert "_quotas" in src, (
        "BRAIN-96 regression: counter must live under "
        "`cur['_quotas']` (settings root). Inside `wizard` "
        "would let BRAIN-80 reset refund the quota."
    )


def test_paid_quota_helper_uses_timezone_aware_utc():
    """Don't regress BRAIN-93's timezone fix — no
    `datetime.utcnow()`."""
    import server as _s
    helper = getattr(_s, "_check_paid_endpoint_quota_async", None) or \
             getattr(_s, "_check_phase5_daily_quota_async", None)
    src = inspect.getsource(helper)
    assert "utcnow" not in src, (
        "BRAIN-96 regression: must not use deprecated "
        "datetime.utcnow()."
    )
    assert (
        "now(timezone.utc)" in src
        or "now(tz=timezone.utc)" in src
    ), (
        "BRAIN-96 regression: must use timezone-aware "
        "datetime.now(timezone.utc)."
    )


def test_paid_quota_blocks_at_or_over_cap():
    """Behavioral: helper must block when count >= cap (NOT >).
    Off-by-one safety."""
    import server as _s
    helper = (
        getattr(_s, "_check_paid_endpoint_quota_async", None)
        or getattr(_s, "_check_phase5_daily_quota_async", None)
    )
    src = inspect.getsource(helper)
    assert ">=" in src, (
        "BRAIN-96 regression: blocking comparison must use "
        ">=, not >. Off-by-one would let one extra call past "
        "the cap."
    )
