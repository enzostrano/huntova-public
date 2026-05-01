# Huntova v0.1.0a26 — 2026-05-01

Fixed a misleading "anonymous usage metrics" checkbox that was
storing a flag nothing read. In local mode the dashboard now
truthfully says telemetry is off by default and points users to the
CLI command if they want to opt in.

## Bug fixes

### Telemetry checkbox no longer lies in local mode
- The Settings → Preferences "Send anonymous usage metrics (default
  on)" checkbox was saving `telemetry_opt_in=true` to user_settings.
  Nothing on the backend reads that flag — it's pure dead code in
  local mode (CLI telemetry is opt-in via `huntova telemetry enable`,
  which writes a separate flag at `~/.config/huntova/.telemetry`).
- In local mode the checkbox is replaced with an honest "Telemetry"
  section: "Off by default. Local CLI doesn't ship anything anywhere
  unless you run `huntova telemetry enable` in your terminal."
- The original checkbox stays in cloud mode (gated `hv-saas-only`)
  since cloud users may still want to express intent, even if the
  flag isn't fully wired backend-side yet.

## Updates
- None — pure bug fix.

## Known issues
- The cloud-side `telemetry_opt_in` flag is still not consulted by
  any backend code. Either wire it to gate `_emit_server_metric()`
  or strip the cloud-mode checkbox too.
- Mobile sidebar drawer still TODO.
