# Huntova v0.1.0a16 — 2026-04-30

Wizard-as-option release. Promoted the auto-wizard to a discoverable
banner instead of a forced first-run modal. Pros land on a clean
dashboard and reach Settings directly; newbies still get the magic
button.

## Updates

### 🪄 Auto Wizard banner (Enzo's killer feature)
- Empty-state banner on first paint: "First time? Run the Auto Wizard
  for a guided 90-second setup." Two buttons: 🪄 Auto Wizard or
  Configure in Settings.
- Banner toggles based on `/api/setup/status` — if any provider is
  configured, banner self-hides. Re-appears if you wipe all keys.
- Replaces the legacy forced-wizard auto-fire (which still triggers
  manually via `Settings → Profile → Retrain`).

### `huntova serve` lands on dashboard, not wizard
- `cmd_serve` in `cli.py` now opens `/` directly. Wizard is
  reachable via `/setup` for the auto-wizard CTA, or via the new
  `--force-setup` flag for explicit re-runs.

## Bug fixes

- `app.js`: legacy `iwiz` wizard was auto-firing 600ms after page
  load via `setTimeout`, producing a double-wizard alongside the new
  banner. Disabled the auto-fire — `iwiz` remains accessible via
  `wizReopen()` button + Settings → Retrain AI.

## Known issues
- Settings modal still missing Providers tab (lands a17+).
- Sidebar version string still hardcoded (lands a17+).
