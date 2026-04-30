# Huntova v0.1.0a42 — 2026-05-01

**Real broken pipeline fix.** SMTP credentials saved via Settings →
Outreach in the dashboard were silently invisible to
`huntova outreach send`. Users would configure SMTP, click Test
SMTP (works), then run outreach and hit "SMTP not configured." Now
fixed.

## Bug fixes

### `email_service.py` reads SMTP env at call time
- Was: `from config import SMTP_HOST, SMTP_PORT, SMTP_USER, ...` at
  module-import time. config.py reads `os.environ.get` at *its* own
  import time. So SMTP values were frozen to whatever was in env when
  the Python process started — env vars added later (e.g. by
  cmd_outreach hydrating from user_settings) didn't propagate.
- Now: new `_smtp_settings()` helper reads the env on every call.
  `_send_email_sync()` and `is_email_configured()` both go through it.
- Module no longer imports SMTP constants from config.py — env-only.

### `cmd_outreach` hydrates SMTP from user_settings + keychain
- Was: only checked `os.environ.get("SMTP_HOST"/"SMTP_USER"/"SMTP_PASSWORD")`
  for the readiness gate. Dashboard-saved settings live in
  user_settings (DB) for host/user/port and the OS keychain for
  the password — neither reaches env vars by default.
- Now: `cmd_outreach` reads `db.get_settings(user_id)` and
  `secrets_store.get_secret("HV_SMTP_PASSWORD")` upfront, populates
  the env vars, then runs the readiness gate. Combined with the
  email_service fix, dashboard SMTP now actually works for
  `huntova outreach send`.
- Updated the error message: "Set it in Settings → Outreach (dashboard)
  or via SMTP_HOST / SMTP_USER / SMTP_PASSWORD env vars."

## Updates
- None.

## Known issues
- Same as a41.
