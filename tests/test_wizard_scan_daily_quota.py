"""Regression tests for BRAIN-92 (a461): /api/wizard/scan must
enforce a persistent per-user daily quota in addition to the
BRAIN-91 short-term rate bucket.

Failure mode (Per Huntova engineering review on metered-API
quota guidance):

BRAIN-91 (a460) gave each wizard endpoint its own
per-minute bucket. `/api/wizard/scan` is capped at 8/min so
bursts are throttled. But a patient attacker (or a buggy
client looping on a backoff timer) can stay under 8/min
indefinitely and still burn hundreds of dollars of BYOK spend
over a day:

- 8 scans/min × 60 min × 24 hr = 11,520 scans/day.
- Each scan crawls up to 200 pages + invokes Gemini Pro on
  ~14k chars of text. Roughly $0.05 / scan.
- Worst case daily BYOK drain on the user's own key: ~$576.

Rate limits control burst speed; quotas cap long-horizon
usage. Both are needed for metered-API protection.

Invariants:
- A new persistent per-user daily quota is enforced on
  `/api/wizard/scan`. The store survives across requests
  (in-memory dict for local mode is acceptable; the cap is
  per-process which is fine because Huntova local-mode is
  single-user and cloud-mode runs the same process).
- Quota is checked BEFORE the crawl + AI call so denials are
  cheap.
- Default cap: 50 scans/day. Configurable via constant.
- Quota window resets daily on UTC date boundary.
- Distinct response shape: HTTP 429 with `error_kind:
  "daily_quota_exceeded"` and a hint about when the quota
  resets, so the UI can show a different toast than the
  per-minute rate-limit response.
- Quota check fires AFTER auth + bucket check (so abuse
  attempts still have to authenticate + hit the per-minute
  cap before consuming quota slots).
"""
from __future__ import annotations
import inspect


def test_scan_endpoint_enforces_daily_quota():
    """Source-level: api_wizard_scan must check a daily quota
    in addition to the BRAIN-91 per-minute bucket."""
    from server import api_wizard_scan
    src = inspect.getsource(api_wizard_scan)
    has_quota = (
        "_check_scan_daily_quota" in src
        or "_scan_daily_quota" in src
        or "daily_quota" in src
        or "_SCAN_DAILY_MAX" in src
    )
    assert has_quota, (
        "BRAIN-92 regression: /api/wizard/scan must enforce a "
        "daily quota. Per-minute rate limit alone allows "
        "indefinite slow burn through user's BYOK wallet."
    )


def test_quota_check_runs_before_external_work():
    """Source-level: quota check must fire BEFORE the
    `_crawl_site_full_sync` or `_fetch_site_text_sync` calls
    so denials are cheap (no wasted crawl)."""
    from server import api_wizard_scan
    src = inspect.getsource(api_wizard_scan)
    quota_idx = -1
    for needle in ("_check_scan_daily_quota", "_scan_daily_quota",
                   "daily_quota", "_SCAN_DAILY_MAX"):
        i = src.find(needle)
        if i != -1:
            quota_idx = i if quota_idx == -1 else min(quota_idx, i)
    crawl_idx = src.find("_crawl_site_full_sync")
    if crawl_idx == -1:
        crawl_idx = src.find("_fetch_site_text_sync")
    assert quota_idx != -1
    assert crawl_idx != -1
    assert quota_idx < crawl_idx, (
        "BRAIN-92 regression: quota check must fire BEFORE the "
        "crawl. Otherwise a denied request still wastes the "
        "200-page fetch + AI summarization spend."
    )


def test_quota_response_uses_distinct_error_kind():
    """Source-level: the quota-exceeded response must carry
    `error_kind: "daily_quota_exceeded"` (or equivalent) so
    the UI distinguishes it from the per-minute 429."""
    from server import api_wizard_scan
    src = inspect.getsource(api_wizard_scan)
    has_distinct_kind = (
        "daily_quota_exceeded" in src
        or "scan_quota_exceeded" in src
        or "quota_exceeded" in src
    )
    assert has_distinct_kind, (
        "BRAIN-92 regression: quota response must carry a "
        "distinct error_kind so the UI can show 'You've used "
        "your scans for today' instead of the generic 'Too "
        "many requests, wait a moment'."
    )


def test_quota_helper_exists_and_is_callable():
    """The quota helper must be exposed on `server` so future
    endpoints (or tests) can call it directly without going
    through HTTP."""
    import server as _s
    helper = getattr(_s, "_check_scan_daily_quota", None)
    assert helper is not None and callable(helper), (
        "BRAIN-92 regression: server must expose "
        "`_check_scan_daily_quota(user_id)` as a callable. "
        "Centralizes the quota logic and makes future routes "
        "(e.g. a future image-scan endpoint) trivial to wire up."
    )


def test_quota_helper_blocks_after_limit():
    """Behavioral: the helper must return True (block) once
    the user crosses the daily limit. Use a synthetic user
    ID + the public quota-max constant if exposed."""
    import server as _s
    helper = _s._check_scan_daily_quota
    test_uid = 999_999_990
    # First call: not blocked (fresh window).
    assert helper(test_uid) is False, (
        "BRAIN-92 regression: first scan must not be blocked."
    )
    # Exhaust the daily allowance. Read the cap if exposed,
    # otherwise default to a generous 100 (will run forever
    # if the cap is missing).
    cap = getattr(_s, "_SCAN_DAILY_MAX", 50)
    blocked = False
    for _ in range(cap + 5):
        if helper(test_uid):
            blocked = True
            break
    assert blocked, (
        f"BRAIN-92 regression: helper never blocked after "
        f"{cap + 5} calls. Daily quota isn't enforcing."
    )


def test_default_quota_is_reasonable_for_normal_use():
    """The cap must be tight enough to matter ($ < $5/day at
    ~$0.05/scan = 100 scans/day max) but generous enough that
    a normal user demoing Huntova doesn't trip it."""
    import server as _s
    cap = getattr(_s, "_SCAN_DAILY_MAX", None)
    assert cap is not None, (
        "BRAIN-92 regression: `_SCAN_DAILY_MAX` constant must "
        "be exposed on server module."
    )
    assert 10 <= cap <= 200, (
        f"BRAIN-92 regression: daily cap {cap} is unreasonable. "
        f"Should be ~25-100. Below 10 frustrates demo users; "
        f"above 200 lets a slow-burn attacker drain $10+/day."
    )
