# Huntova v0.1.0a37 — 2026-05-01

Hunts launched without an AI provider configured used to silently
crash inside the agent thread (visible only as SSE log events).
Now `agent_runner.start_agent()` does a fast, sync provider
pre-flight before spawning the thread and returns a clear error
message that the existing frontend toast surfaces immediately.

## Bug fixes

### Hunt-launch pre-flight rejects no-provider in local mode
- `agent_runner.start_agent` now calls
  `providers.list_available_providers()` after the credit gate,
  before spawning the thread. Empty list → returns
  `{"ok": false, "error": "No AI provider configured. Open Settings
  → Providers to add a key."}`.
- The pre-flight is wrapped in try/except — if it itself fails
  (import error, weird env), the call falls through to the original
  behavior so we don't break the path.
- Local-mode-only — cloud routes still use their existing provider
  routing so we don't second-guess the cloud config pipeline.

## Verified live (Playwright)
- ✓ `POST /agent/control {action:'start', countries:['Germany']}`
  with no provider configured returns immediately with
  `ok:false, error:"No AI provider configured. Open Settings →
  Providers to add a key."` (HTTP 200, agent thread never spawned)

## Updates
- None.

## Known issues
- Same as a36.
