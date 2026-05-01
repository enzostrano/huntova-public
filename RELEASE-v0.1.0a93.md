# Huntova v0.1.0a93 — 2026-05-01

## Bug fixes

### `_run_log` in-memory tail capped at 1 000 entries
- `UserAgentContext._run_log` (the per-user list `emit_log` writes
  every line into) had no cap. A long hunt over 500–1000 leads emits
  4 000–6 000 log lines, all retained in RAM for the lifetime of the
  context, which in cloud / multi-user mode keeps growing across
  hunts. The on-disk session log already records the full transcript
  via the cached file handle, so the in-memory copy was duplicate
  state — it only exists to power the post-run summary.
- Cap at 1 000 entries; on overflow, drop the oldest 200 in one
  amortised pass so we aren't paying eviction cost on every line.

### `/api/wizard/scan` no-cache header
- The wizard's URL-scan endpoint returned the AI-extracted analysis
  dict without any `Cache-Control`. Browsers / shared proxies could
  cache the response, so a returning user could see another user's
  scan result in shared cloud deployments behind a CDN.
- Now sets `Cache-Control: no-store, no-cache, must-revalidate, private`
  on every response (success, fallback, and exception paths).

## Updates
- None.

## Known issues
- Same as a92.
