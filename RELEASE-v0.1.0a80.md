# Huntova v0.1.0a80 — 2026-05-01

80th release.

## Bug fixes

### `crm_refresh` SSE handler wrapped in try/catch
- The `crm_refresh` event listener was the only handler in the
  SSE bundle without a try/catch. A bug in `loadCRM()` (network
  error, malformed lead in the response) would propagate up and
  potentially break the rest of the SSE message dispatch loop.
- Now: `()=>{try{loadCRM()}catch(_){}}` — same defensive pattern
  every other handler uses.

## Process notes
- Two agent findings skipped this round:
  - SearXNG view-counter "race" — `UPDATE col = col + 1` is atomic
    in both Postgres and SQLite; no race exists.
  - SSRF DNS-rebinding — already in the deferred-bug list (memory
    captures it as "DNS-rebinding TOCTOU in is_private_url SSRF
    guard, round 47, needs IP pinning + custom HTTPAdapter + SNI").

## Updates
- None.

## Known issues
- Same as a79.
