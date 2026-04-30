# Huntova v0.1.0a18 — 2026-04-30

Three sidebar bug fixes caught in user QA on a17.

## Bug fixes

### Sidebar version is now live
- Was hardcoded to "0.1.0a17" in the template. Now `app.js` fetches
  `/api/runtime` and writes the response's `version` field into
  `#hvSbVer` at load time.
- Means future releases never have a stale version pill again.

### Topnav logo no longer duplicates sidebar brand
- At ≥900px the sidebar already shows HUNTOVA branding. The topnav's
  `.topnav-left .topnav-logo` and `.topnav-left .dot` were still
  rendering, producing a double-brand look.
- CSS: hide both at ≥900px. Mobile (<900px) keeps the topnav logo
  since the sidebar is hidden there.

### Sidebar external links don't eject the dashboard
- `/plugins`, `/demo`, `/setup` are standalone pages with no sidebar
  shell. Clicking them was leaving users stranded.
- Added `target="_blank"` + a small ↗ arrow to the row labels so
  users see "this opens in a new tab" before clicking.

## Known issues
- Email-verification banner still re-shows on `/api/account` poll in
  local mode (auto-bootstrapped local user has `email_verified=false`).
  Lands a19.
- "credit"-related copy still leaking in some empty states (lands
  a19).
