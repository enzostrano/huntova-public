# Huntova v0.1.0a31 — 2026-05-01

Profile → Your Name now drives the dashboard greeting in local
mode. Was: greeting forever read your OS username (a24) even after
you typed a different name in Settings → Profile. Now it mirrors.

## Updates

### Profile name → dashboard greeting (local mode)
- Backend: `POST /api/settings` in `single_user_mode` now mirrors
  the saved `from_name` value into `users.display_name` (capped at
  80 chars). Cloud users keep their own display_name pipeline.
- Frontend: `saveSettings()` re-fetches `/api/account` after a
  successful save so `_hvAccount.display_name` updates in place.
  Then `hvUpdateGreeting()` re-renders the `dashHi` element. No
  reload required.
- Refactored the greeting one-shot setter into a callable
  `hvUpdateGreeting()` function so any path that updates the
  account can re-render the greeting.

## Bug fixes
- (Wraps a quality-of-life ask, not a regression.)

## Verified live (Playwright, full round-trip)
- ✓ Initial greeting: "Working late, Enzomacbook! 👋" (OS username)
- ✓ Open Settings → Profile → Your Name = "Enzo Strano" → Save
- ✓ Greeting immediately reads: "Working late, Enzo Strano! 👋"
- ✓ No page reload, no flash, settings modal closes cleanly

## Known issues
- Cloud-side `telemetry_opt_in` flag still not consulted.
- The avatar/initial in the user menu doesn't update in local mode
  (that codepath is gated behind `!single_user_mode` to preserve
  the gear icon — could be reworked but the gear is intentional).
