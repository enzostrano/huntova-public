# Huntova v0.1.0a50 — 2026-05-01

50th release. Rate-limit added to wizard save-progress endpoint.

## Bug fixes

### `/api/wizard/save-progress` now rate-limited
- Was: the endpoint had no rate limit. Sibling endpoints
  `/api/wizard/scan` and `/api/wizard/assist` already use
  `_check_ai_rate()`. A chatty client (or bot in cloud mode)
  could hammer this endpoint with rapid wizard-state writes,
  ballooning the user_settings JSON column + thrashing SQLite's
  WAL log.
- Now: same `_check_ai_rate(user["id"])` gate as the sibling
  wizard endpoints. Returns 429 on excess.

## Updates
- None.

## Known issues
- Same as a49.
