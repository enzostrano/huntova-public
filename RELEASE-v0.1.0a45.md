# Huntova v0.1.0a45 — 2026-05-01

`POST /api/settings` now mirrors keychain saves + SMTP/webhook
fields into `os.environ` so plugins reading via env see dashboard
changes immediately — no process restart required.

## Bug fixes

### Dashboard saves now propagate to plugin runtime
- Was: dashboard saved a fresh slack webhook URL → keychain
  updated, but `os.environ["HV_SLACK_WEBHOOK_URL"]` stayed at the
  process-startup value (or empty if first-run). Plugin read env at
  agent run → fired with stale URL or no-op'd entirely. Same gap
  for `HV_WEBHOOK_SECRET` (generic-webhook) and `HV_SMTP_PASSWORD`
  (email_service).
- Now: `_SECRET_MAP` save loop also `os.environ[_name] = _v` after
  successful keychain write (or `os.environ.pop` on delete).
- Same fix for non-secret SMTP host/user/port + webhook_url —
  dashboard save mirrors to `SMTP_HOST` / `SMTP_USER` / `SMTP_PORT`
  / `HV_WEBHOOK_URL` env so plugins + email_service see fresh
  values immediately.

## Updates
- None.

## Known issues
- Same as a44.
