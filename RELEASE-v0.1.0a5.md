# Huntova v0.1.0a5 — 2026-04-30 (third drop today)

The "actually-secure + actually-installable" release. Plus migrate
from Apollo / Clay / Hunter, plus first-run polish.

## Updates

### `huntova migrate` — bulk import from Apollo / Clay / Hunter / CSV
- New `cli_migrate.py` module wired via `cli.py`.
- Subcommands:
  - `huntova migrate from-csv <path>` — generic CSV import with header
    auto-detect (org_name / domain / contact_email / fit_score / etc.)
  - `huntova migrate from-apollo <path>` — Apollo column map (Company /
    Website / First Name / Last Name / Title / Email / LinkedIn URL)
  - `huntova migrate from-clay <path>` — Clay enrichment column map
  - `huntova migrate from-hunter <path>` — Hunter.io email-finder export
  - `huntova migrate stats <path>` — dry-run, show row count + detected
    columns + how they'd map without writing
- BOM-tolerant (`utf-8-sig` so Excel/Sheets exports work).
- Idempotent — pre-loads existing leads, dedupes by `(org_website,
  contact_email)` tuple. `--force` overwrites.
- `--map csv_col=lead_field` repeatable flag for manual column mapping.
- Pattern adapted from openclaw/openclaw migrate.

### First-run polish
- `huntova --version` flag now works alongside `huntova version`. Was
  one of the most-typed commands and threw "unrecognized arguments".
- Onboard auto-generates `HV_SECRET_KEY` on first run and saves it to
  the keychain. No more SEV-2 finding fired by `huntova security
  audit` immediately after install.
- Onboard tightens `db.sqlite` perms to 0600 if it already exists.
  Same — no more SEV-2 audit finding on the vanilla install.
- `_hydrate_env_from_local_config` extended to round-trip
  `HV_SECRET_KEY` across processes so the dev-fallback warning
  doesn't keep firing.

## Bug fixes (security)

- **SEV-1 — SSRF in `/api/webhooks/test`.** A logged-in user could
  save `webhook_url=http://169.254.169.254/...` and click Test to
  exfiltrate AWS metadata, or
  `http://postgres.railway.internal:5432/` to probe the internal
  VPC. Fix: `app.is_private_url()` gate before the POST. Returns 400
  blocked_target on private/loopback/link-local IPs.
- **SEV-2 — SMTP test internal-port-scan oracle.** Differentiated
  `connect_failed` vs `auth_failed` responses revealed which internal
  ports were open. Fix: restrict ports to {25, 465, 587, 2525} +
  `is_private_url()` gate on the SMTP host.
- **SEV-2 — No rate limit on test endpoints.** A hijacked session
  could credential-stuff via `/api/smtp/test`. Fix: 5 calls / minute
  per (user, endpoint) via new `_check_test_endpoint_rate`.
- **SEV-2 — `/api/settings` GET leaked secrets.** GET returned
  `{**DEFAULT_SETTINGS, **s}` raw; any legacy DB row holding
  `smtp_password` or `webhook_secret` (from before v0.1.0a4 routed
  them to keychain) leaked to the browser on every load. Fix: strip
  smtp_password / webhook_secret / plugin_slack_webhook_url before
  returning.
- **SEV-2 — Account export missing `plugin_slack_webhook_url` strip.**
  The downloaded JSON bundle could contain a Slack webhook URL stored
  in legacy settings. Fix: extended strip list.

## Bug fixes (UX)

- **`--reset-scope keys`** now also pops env vars from `os.environ`.
  Was carried over by stale shell-exported keys that the wizard then
  treated as already-saved.
- **Chat `start_hunt` forwards `timeout_minutes`** (clamped [1,120])
  to `agent_runner` budget cap. Previously dropped silently.
- **Test suite default flipped to Anthropic.** `test_get_provider_
  picks_anthropic_by_default` is the new primary test;
  gemini-only-key path retained as a separate test.

## Known bugs (still to fix)

- **MED — `/favicon.ico` returns 404** on every page load (Sev 3).
  Drop a 1x1 SVG / set `<link rel="icon" href="data:,">` in shared
  base template. Cosmetic but throws a console error.
- **LOW — Password input on /setup not in `<form>`** so password
  managers don't behave correctly. Wrap the provider/key block.
- **LOW — Landing nav has too many CTAs.** `Try it free` + `CLI` +
  `Get started` — three buttons doing similar things. Consolidate to
  one primary `Get started`.
- **LOW — Hunt timeout still iteration-bounded.** Per-URL
  `_check_budget` probe in `app.py:qualify` is the post-launch fix.
- **LOW — `recipe-adapter` / `adaptation-rules` plugins still only
  fire on CLI `recipe run`.** The DNA-prompt feedback wiring (v0.1.0a4)
  is the primary smart-loop now; the adapter plugins are secondary.

## Repo / release process

- Pushes go to `enzostrano/huntova-public` ONLY.
- Each release ships `RELEASE-v<version>.md` at the repo root.
- CHANGELOG.md is the human-readable summary.

## Credits

**Brain:** Enzo (@enzostrano).

**Coding:** Claude (Anthropic), via Claude Code.

Thank you Anthropic. Claude is the default provider — it's the model
that shipped this thing.
