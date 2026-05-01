# Huntova v0.1.0a23 — 2026-05-01

Caught a sidebar bug in the live-Playwright audit: clicking a
sidebar nav row was switching the dashboard page but the active
highlight was never moving off "Overview". Two-line fix + bonus
sync helper for the topnav fallback path.

## Bug fixes

### Sidebar active state actually moves now
- `hvSbGo()` was selecting `.hv-sb-btn` to toggle the `.on` class.
  The sidebar uses `.hv-sb-row` (and only data-page rows are
  navigable). Selector matched zero elements → `Overview` stayed
  highlighted forever.
- Fixed selector to `.hv-sb-row[data-page]`. Active state moves
  correctly when clicking Dashboard / Leads / Hunts.

### Sidebar stays in sync when other paths change the page
- Quick-action cards on the dashboard call `[data-page=crm].click()`
  on the topnav nav-button to switch pages. That bypassed the
  sidebar update.
- Added a delegated click listener on `.topnav-centre .nav-btn[data-page]`
  that mirrors the active-row class onto `.hv-sb-row`. Whether you
  click sidebar, topnav, or a quick-action card, the sidebar
  highlight follows.

## Updates
- None — all bug fixes.

## Verified live (Playwright smoke test)
- ✓ Dashboard renders with Providers stat card + onboard banner
- ✓ Sidebar version reads `0.1.0a22` from `/api/runtime` (live, not
  hardcoded)
- ✓ Settings → Providers tab renders all 12 providers with status
  pills + "Get key ↗" deeplinks
- ✓ Settings → Data tab (was "Account / Data" in cloud)
- ✓ Chat slideover opens and `POST /api/chat` round-trips correctly
- ✓ Sidebar Leads / Hunts active state now follows clicks (this
  release's fix)

## Known issues
- `CLAUDE.md` still legacy SaaS spec (a24+).
- Mobile sidebar drawer (<900px) still TODO.
