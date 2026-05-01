# Huntova v0.1.0a69 — 2026-05-01

## Bug fixes

### Stop-flag per-URL latency fix
- The agent's inner per-URL loop only checked `_check_budget()`,
  not `_check_stop()`. User clicking Stop mid-result-set had to
  wait for the entire inner loop (fetch + deep_qualify + AI
  scoring + enrichment) to drain before the outer query loop saw
  the stop flag. Could be 30–60s of "running" UI after a Stop click.
- Now: `_check_stop()` runs per-URL at loop top. Stop registers
  within ~2–3s — the time of one in-flight URL fetch.

## Updates
- None.

## Known issues
- Same as a68.
