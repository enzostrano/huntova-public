# Huntova v0.1.0a91 — 2026-05-01

## Bug fixes

### Jina fallback logs the 429-rate-limit case
- The `r.jina.ai` JS-fallback fetcher silently treated every non-200
  response — including a 429 — as "no content," so users on a heavy
  hunt could never tell why Playwright-style sites suddenly stopped
  yielding text. Operators only saw "low extraction quality."
- Now branches `429` first and emits an explicit `"Jina rate-limited"`
  warn-level log line with the URL prefix. Behaviour is otherwise
  unchanged (still returns `("", "")` so the pipeline degrades
  gracefully); this is purely an observability fix.

## Updates
- None.

## Known issues
- Same as a90.
