# Huntova v0.1.0a6 — 2026-04-30 (fourth drop today)

The "no more silent failures" release. Plus the `huntova approve`
manual-review queue, plus 3 cosmetic install-test polish items.

## Updates

### `huntova approve` — manual-review queue
- New `cli_approve.py` module (280 lines) wired via `cli.py`.
- `huntova approve queue` — table of high-fit + awaiting-approval
  leads, sorted by fit_score. `--json` for scripted use.
- `huntova approve <lead_id>` — flip `status="approved"`. Side
  effect: the next `huntova outreach send` releases it.
- `huntova approve --top N` — bulk-approve top-N highest-fit pending.
- `huntova approve --reject <lead_id>` — `status="rejected"` + writes
  audit row in `lead_actions`. Counts as bad-fit feedback for the
  smart-loop.
- `huntova approve diff <lead_id>` — side-by-side: AI draft email
  vs the source evidence quote that justified the score. Sanity-
  check before approving.
- Sending stays in `huntova outreach send`. Approve only mutates
  status. Pattern adapted from openclaw approvals.

### Cosmetic install-test polish
- `/favicon.ico` 404 fixed — blanket route in `server.py` returns
  the existing `static/favicon-32x32.png`. No more browser-console
  noise on every page load.
- `/setup` API-key field wrapped in `<form>` so password managers
  (1Password, LastPass, Bitwarden) suggest/save credentials.
- Landing nav `CLI` link hidden on desktop (still in mobile drawer +
  footer Product column). Removes a redundant CTA.

## Bug fixes (security + reliability)

- **CRIT — `HV_SECRET_KEY` autogen silent failure.** v0.1.0a5 wrapped
  `set_secret(...)` in `except Exception: pass`. If the keychain
  write failed (locked macOS keychain, dismissed prompt, kwallet not
  running), the env var was never set and the next CLI run
  regenerated a fresh key, **invalidating every signed session/cookie**.
  Fix: always set `os.environ["HV_SECRET_KEY"]` first, surface the
  keychain failure to stderr with a remediation hint.
- **HIGH — `_check_test_endpoint_rate` unbounded-growth memory leak.**
  v0.1.0a5 introduced the rate limiter without the GC sweep that
  `_check_export_rate` already had (multi-agent bug #37 pattern).
  Long-running cloud deploys would creep until OOM-kill. Fix: same
  5-min sweep dropping users with empty windows.
- **HIGH — `huntova migrate` OOMs on large CSVs + asyncio churn.**
  `_print_preview` did `rows = list(reader)` slurping the whole CSV
  (500MB Apollo export → CLI OOM). Plus `_asyncio.run(...)` per
  row spawned 50k event loops on a 50k-row import, starving the
  PostgreSQL pool. Fix: streaming preview (only read first row +
  count rest); single asyncio loop wraps the whole import.
- **MED — SMTP test oracle re-leaked via error message.** Port
  allowlist + private-IP gate were both correct, but `str(e)[:120]`
  echoed "Connection refused" / "timed out" to the response. Fixed
  with a single canonical "SMTP connection failed (host/port/firewall)"
  message.

## Known bugs (still to fix — moved to ROADMAP)

- `_hydrate_env_from_local_config` keychain-error log fires every
  CLI run (no once-flag). Cosmetic. Cache to `.keychain_warned`
  sentinel file.
- `is_private_url` returns True on DNS resolution failure but uses
  the same "blocked_target" message — confusing for users with
  transient DNS. Differentiate.
- Hunt timeout fires only at iteration boundaries (carry-over from
  v0.1.0a4).
- `recipe-adapter` plugins fire only on CLI `recipe run` (carry-
  over).

## Repo / release process

- Pushes go to `enzostrano/huntova-public` ONLY.
- Each release ships `RELEASE-v<version>.md` at the repo root.
- CHANGELOG.md is the human-readable summary.

## Credits

**Brain:** Enzo (@enzostrano).

**Coding:** Claude (Anthropic), via Claude Code.

Thank you Anthropic.
