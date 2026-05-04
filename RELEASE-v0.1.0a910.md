# Huntova v0.1.0a910 — search abstraction + retry policy audit

Search-pipeline robustness sweep on `app.py`. Three real-bug fixes batched
per the release-cadence standing order.

## Bug fixes

- **SearXNG 5xx silently swallowed (no retries, no SSE warning).**
  `search()` only handled `Timeout` and `ConnectionError` explicitly;
  `r.raise_for_status()` for 502/503 raised `HTTPError`, which fell
  through the bare `except Exception` branch and the agent dropped to
  the DDG fallback after a single attempt. A flaky self-hosted
  SearXNG (or transient 502 during a restart) burned the entire
  hunt's quota by hammering DDG, which has its own rate limits — so
  cascading failures dropped lead volume to zero with no signal to
  the user. Now `_searxng_query_once` classifies every HTTP outcome
  (`http_5xx` / `http_4xx` / `timeout` / `conn` / `decode`),
  `search()` retries retryable failures within a per-query budget
  (`SEARXNG_MAX_RETRIES_PER_QUERY`, default 2) with exponential
  backoff + jitter, and surfaces a clear per-failure SSE log line.
  4xx (other than 429) bails fast — bad query, not a flake.
  (`app.py:5142`, `app.py:5263`)

- **No per-hunt circuit breaker.** Every query independently retried
  even when SearXNG had been unreachable for 30 consecutive queries —
  wasted RTTs, wasted DDG quota, and the user still saw the same
  silent zero-lead outcome. Added `_SearxngBreaker`: after
  `SEARXNG_MAX_CONSEC_FAILURES` consecutive query-level failures
  (default 3), the breaker trips and all subsequent calls skip the
  SearXNG round-trip entirely. A single user-facing SSE warning
  fires the first time the breaker trips ("Search backend degraded —
  switching to DuckDuckGo for the rest of this hunt"), so the user
  knows the hunt is running degraded instead of broken.
  `reset_search_breaker()` is wired into `run_agent()` start so a
  previous hunt's failures don't leak into a new one. (`app.py:5142`)

- **`run_agent()` refused to start when `check_searxng()` returned
  False.** Self-inflicted outage — `search()` has a working DDG
  fallback. Now a failed startup probe pre-trips the breaker (so
  the per-query retry budget isn't paid up front for every query),
  emits one degraded-mode warning, and the agent continues.
  (`app.py:6707`)

## Tests

22 new regression tests in `tests/test_search_retry.py`:

- happy-path SearXNG success, zero-result fallthrough
- 502 / 503 / 429 / timeout / connection-error / JSON-decode all
  retry within budget
- 404 / 400 do NOT retry and do NOT trip the breaker
- breaker trips after N consecutive failures
- breaker resets consecutive-failure count on a single success
- "degraded" warning emitted exactly once per hunt
- `reset_search_breaker()` clears state
- low-level classifier returns the right `error_kind` for each case

All tests stub `_search_session.get`, so no network is hit
— suite finishes in <0.5s.

## Known limitations (deferred)

- Per-domain rate limiting when scoring leads from the same source
  (50+ results from one domain) — not addressed this release.
- Jina Reader fallback in `fetch_page_requests` and `crawl_prospect`
  isn't deduplicated across the two paths — a URL fetched via both
  paths could hit Jina twice — not addressed this release.
- Public-SearXNG-instance discovery / auto-rotation if `searx.be`
  changes — relying on the breaker + DDG fallback for now.
