# Huntova v0.1.0a95 — 2026-05-01

## Bug fixes

### Wizard's "ask the AI" input no longer triggers iOS auto-zoom
- `.iwiz-assist-input input` (the inline-AI text field inside the
  wizard) used `font-size: 13px`. iOS Safari auto-zooms on focus
  whenever an input renders below 16 px, so wizard users on
  iPhone watched the page jerk-zoom on every keystroke focus.
- Added a 16 px override (with a 44 px `min-height` matching the
  WCAG tap-target heuristic) inside the `@media (max-width: 600px)`
  block, mirroring the same fix already in place for
  `.iwiz-chat-input input` and `.nw-input input`.

## Updates
- None.

## Known issues
- Same as a94.
