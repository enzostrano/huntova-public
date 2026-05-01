# Huntova v0.1.0a27 — 2026-05-01

Tiny meta-tag fix caught in the live console-warning audit.

## Bug fixes

### Meta tag deprecation warning silenced
- Browsers were warning `<meta name="apple-mobile-web-app-capable">
  is deprecated. Please include <meta name="mobile-web-app-capable">`.
- Added the modern non-prefixed variant alongside the legacy one
  (Apple still expects the prefixed form on older iOS). Both ship
  now.

## Updates
- None — pure cleanup.

## Verified live
- ✓ All 7 Settings tabs (Profile, Providers, Plugins, Webhooks,
  Outreach, Preferences, Data)
- ✓ /setup wizard (Cloud APIs / Local AI / Custom endpoint)
- ✓ /demo public sample-hunt page
- ✓ /plugins community registry
- ✓ /landing marketing page

## Known issues
- The cloud-side `telemetry_opt_in` flag still isn't read by any
  backend code (carry-over from a26).
- Mobile sidebar drawer (<900px) still TODO.
