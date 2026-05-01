# Huntova v0.1.0a40 — 2026-05-01

Two more AI-using endpoints get the same fail-fast preflight a37
added for hunt-launch. Users without a provider get a clear,
actionable error instead of an opaque AI failure.

## Bug fixes

### `/api/agent-dna/generate` no-provider preflight
- Was: would attempt the AI call inside `generate_agent_dna()`
  and fail with a vendor-specific error message.
- Now: fast `list_available_providers()` check (local mode only).
  Empty list → 400 with `"No AI provider configured. Open Settings
  → Providers to add a key."`

### `/api/research` no-provider preflight
- Same treatment for Deep Research (the per-lead re-scrape +
  AI deep-analysis path).
- Reject early — before the AI rate-limit check uses budget AND
  before any credit deduction in cloud mode.

## Updates
- None.

## Known issues
- Same as a39.
