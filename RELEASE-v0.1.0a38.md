# Huntova v0.1.0a38 — 2026-05-01

The a37 pre-flight told users "Open Settings → Providers to add a
key" via a toast. Now `launchAgent()` actually does it for them —
toast + close start popup + open Settings → Providers in one shot.

## Updates

### `launchAgent` error → auto-opens Settings → Providers
- When `/agent/control` returns the no-provider error, the frontend
  used to just toast the message. The user had to read it, dismiss
  the toast, find Settings, click Providers — three steps just to
  recover.
- Now: toast still fires (so the user knows what happened), then
  `closeStartPopup()` + `openSettings()` + `settingsTab('providers')`
  in one chain. They land on the form ready to paste a key.
- Cloud + credit/upgrade error path unchanged — still opens the
  pricing modal.

## Bug fixes
- None new.

## Verified live (Playwright)
- ✓ openStartPopup() → launchAgent() with no provider configured →
  start popup closes, Settings modal opens, Providers tab is the
  active vtab. Three-step recovery → zero-step recovery.

## Known issues
- Same as a37.
