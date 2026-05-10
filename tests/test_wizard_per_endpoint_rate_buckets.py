"""Regression tests for BRAIN-91 (a460): wizard endpoints must
use per-route rate buckets so heavy scan/phase-5 traffic
doesn't starve lightweight assist/save-progress flows.

Failure mode (Per Huntova engineering review on rate-limiter
fairness):

`_check_ai_rate(user_id)` was a single shared per-user bucket
(20 calls / 60s). Every wizard endpoint shared it:

- `/api/wizard/scan` (expensive: 200-page crawl + ~$0.05 AI)
- `/api/wizard/generate-phase5` (expensive: AI generation)
- `/api/wizard/assist` (medium: AI chat)
- `/api/wizard/save-progress` (cheap: DB write, fired on every
  Continue / Skip / Back click)
- `/api/wizard/complete` (rare expensive: brain + dossier + DNA)
- `/api/wizard/reset` (rare cheap: DB wipe)

A user typing fast through the wizard could fire 15-20
save-progress writes in a minute (Continue every 1.5s),
hitting the cap and being denied a single subsequent
`/api/wizard/assist` or `/api/wizard/scan` request. Self-DoS:
the user's own normal interaction blocks them from
lower-cost (or higher-value) operations.

Standard fix per token-bucket guidance: heterogeneous-cost
endpoints get separate buckets, OR a weighted bucket charges
expensive operations more. Separate buckets are simpler and
catch the fairness invariant cleanly.

Invariants:
- `_check_ai_rate` accepts an optional `bucket` keyword (or
  positional) so callers can opt into a named per-route
  bucket. Default behavior (legacy "ai" bucket) preserved.
- Each wizard endpoint passes its own bucket name.
- Save-progress (high-frequency, low-cost) gets a generous
  budget (>= 60/min) so normal wizard navigation never
  rate-limits.
- Scan + phase-5 + complete (low-frequency, expensive) get
  tighter budgets (≤ 10/min).
- Buckets are isolated — exhausting `wizard_scan` does NOT
  block subsequent `wizard_assist` / `wizard_save_progress`
  calls.
"""
from __future__ import annotations
import inspect
import time


def test_check_ai_rate_accepts_bucket_argument():
    """Source-level: the rate-limiter must accept a bucket
    argument so callers can target named per-route limits."""
    from server import _check_ai_rate
    sig = inspect.signature(_check_ai_rate)
    params = list(sig.parameters)
    assert "bucket" in params, (
        "BRAIN-91 regression: _check_ai_rate must accept a "
        "`bucket` argument so wizard endpoints can target "
        "per-route limits instead of starving each other."
    )


def test_legacy_default_bucket_preserved():
    """Default invocation without bucket should still work
    (legacy non-wizard callsites use _check_ai_rate(user_id))."""
    from server import _check_ai_rate
    # Use a synthetic user_id that won't collide with anything
    # else. Should return False the first time.
    test_uid = 999_999_999
    res = _check_ai_rate(test_uid)
    assert res is False, (
        "BRAIN-91 regression: legacy callsites must still work "
        "without a bucket arg."
    )


def test_buckets_are_isolated():
    """Behavioral: exhausting one bucket does NOT block calls
    on a different bucket. The whole point of the split."""
    from server import _check_ai_rate
    # Use synthetic user IDs to avoid colliding with real users.
    test_uid = 999_999_998
    # Exhaust one bucket. Different buckets have different
    # caps — pick a tight one (assume <=20).
    for _ in range(50):
        _check_ai_rate(test_uid, bucket="wizard_scan")
    # Now hit a different bucket — must succeed at least once.
    other_blocked = _check_ai_rate(test_uid, bucket="wizard_save_progress")
    assert other_blocked is False, (
        "BRAIN-91 regression: buckets must be isolated. "
        "Exhausting `wizard_scan` blocked `wizard_save_progress` "
        "— bucket separation is broken."
    )


def test_save_progress_uses_high_capacity_bucket():
    """Save-progress fires on every Continue/Skip/Back click.
    Its bucket cap must be high enough that a fast typist
    doesn't self-throttle."""
    from server import _check_ai_rate
    test_uid = 999_999_997
    # 30 saves in quick succession should all pass — that's
    # a fast user navigating ~30 questions in a minute.
    blocked = 0
    for _ in range(30):
        if _check_ai_rate(test_uid, bucket="wizard_save_progress"):
            blocked += 1
    assert blocked == 0, (
        f"BRAIN-91 regression: save-progress bucket too tight "
        f"— {blocked}/30 normal Continue clicks rate-limited. "
        f"Cap should be >= 60/min for this high-frequency "
        f"low-cost endpoint."
    )


def test_scan_uses_strict_bucket():
    """Scan is expensive (~$0.05 + 200-page crawl). Its bucket
    must be strict so a tab-spamming user can't drain their own
    BYOK wallet."""
    from server import _check_ai_rate
    test_uid = 999_999_996
    # ≤ 10 successful scans per minute. Allow some flexibility
    # but require *some* throttling within 30 attempts.
    blocked = 0
    for _ in range(30):
        if _check_ai_rate(test_uid, bucket="wizard_scan"):
            blocked += 1
    assert blocked > 0, (
        "BRAIN-91 regression: scan bucket isn't throttling. "
        "30 rapid scans should trigger at least one rate-limit "
        "to protect the user's BYOK wallet."
    )


def test_wizard_save_progress_endpoint_uses_save_bucket():
    """Source-level: the save-progress endpoint must pass the
    `wizard_save_progress` bucket name (or equivalent), not
    rely on the default."""
    from server import api_wizard_save_progress
    src = inspect.getsource(api_wizard_save_progress)
    has_bucket = (
        'bucket="wizard_save_progress"' in src
        or "bucket='wizard_save_progress'" in src
        or 'bucket="wizard_save"' in src
        or "bucket='wizard_save'" in src
    )
    assert has_bucket, (
        "BRAIN-91 regression: save-progress must use its own "
        "`wizard_save_progress` (or `wizard_save`) bucket."
    )


def test_wizard_scan_endpoint_uses_scan_bucket():
    """Source-level: scan endpoint must pass the wizard_scan
    bucket so heavy scan traffic can't starve assist/save."""
    from server import api_wizard_scan
    src = inspect.getsource(api_wizard_scan)
    assert 'bucket="wizard_scan"' in src or "bucket='wizard_scan'" in src, (
        "BRAIN-91 regression: scan endpoint must use the "
        "wizard_scan bucket."
    )


def test_wizard_phase5_endpoint_uses_phase5_bucket():
    """Source-level: generate-phase5 must use its own bucket."""
    from server import api_wizard_generate_phase5
    src = inspect.getsource(api_wizard_generate_phase5)
    has_bucket = (
        'bucket="wizard_phase5"' in src
        or "bucket='wizard_phase5'" in src
        or 'bucket="wizard_generate_phase5"' in src
    )
    assert has_bucket, (
        "BRAIN-91 regression: generate-phase5 must use its own "
        "wizard_phase5 bucket."
    )


def test_wizard_assist_endpoint_uses_assist_bucket():
    """Source-level: assist must use its own bucket so a user
    chatting to refine answers isn't blocked by save-progress
    burst from rapid Continue clicks."""
    from server import api_wizard_assist
    src = inspect.getsource(api_wizard_assist)
    has_bucket = (
        'bucket="wizard_assist"' in src
        or "bucket='wizard_assist'" in src
    )
    assert has_bucket, (
        "BRAIN-91 regression: assist must use its own "
        "wizard_assist bucket."
    )
