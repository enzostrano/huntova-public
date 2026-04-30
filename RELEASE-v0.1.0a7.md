# Huntova v0.1.0a7 — 2026-04-30 (fifth drop today)

The "loop never stops" release. Hunt-timeout-mid-query (long-standing
known bug from v0.1.0a4) is finally fixed. Plus `huntova recipe
export/import/diff` for sharing hunt configs, and 4 round-6 audit
fixes including a Sev-1 mobile-nav regression.

## Updates

### Hunt timeout fires mid-query (was iteration-bounded)
- `app.py:_check_budget()` probes added at 3 points:
  - Top of `for r in results:` per-URL loop in main agent run
  - Inside `deep_qualify()` per-sub-page loop
  - Post-batch guard so the outer query loop exits cleanly
- Both `max_leads` and `timeout` caps now fire within ~30s of the
  user-set deadline (previously waited for the next query iteration,
  which could be 10+ minutes if the agent was mid-Playwright deep-
  qualify on one URL).
- Long-standing known bug from v0.1.0a4 / a5 / a6 — now closed.

### `huntova recipe export / import / diff`
- New CLI verbs to share a hunt config across machines/teammates.
- `recipe export [--name N] [--out PATH]` — dump wizard + scoring
  rules + plugin config + preferred provider as portable TOML.
  Auto-strips secrets (`*_password`, `*_key`, `*_token`,
  `*_webhook*`). Default out: `~/huntova-<name>-<date>.toml`,
  mode 0600.
- `recipe import <path> [--force]` — parse TOML, merge into user
  settings via `db.merge_settings` (row-locked RMW), save recipe
  row, regenerate Agent DNA.
- `recipe diff <local-name> <imported-path>` — plain-text +/- diff
  showing what changes when you import.
- Pattern adapted from OpenClaw recipe export/import. NOTICE.md
  updated.

## Bug fixes

- **CRIT — Mobile nav CLI link hidden by accident.** v0.1.0a6
  `.n-cli-link{display:none}` had specificity (0,2,0) which beat
  the mobile media-query (0,1,1), so the CLI link was hidden on
  mobile too. Fix: wrap in `@media(min-width:901px){...}`. Mobile
  drawer now correctly shows it.
- **HIGH — `huntova approve --top N` spawned 2N asyncio loops.**
  Same anti-pattern v0.1.0a6 fixed in cli_migrate. Bulk-approve of
  100 leads = 200 loop creations + 200 PG-pool checkouts. Fix:
  single `_run_bulk()` async coroutine wraps the whole batch.
- **MED — `/favicon.ico` 500 on missing file + no cache.** Returns
  204 if the file disappears (broken deploy) instead of leaking
  the absolute server path. Adds `Cache-Control: public, max-age=
  86400` so repeat visits don't re-fetch.
- **MED — CSP missing `frame-ancestors 'none'`.** Added to the
  global response-header CSP in server.py middleware. Closes the
  clickjacking surface.
- **LOW — `tests/test_providers.py` regex mismatch.** v0.1.0a3
  changed the no-key error message but the test still asserted
  the old string. Fixed; all 72 tests pass.

## Known bugs (still to fix)

- `_hydrate_env_from_local_config` keychain warning fires every
  CLI run (no once-flag). Cosmetic. Cache to sentinel file.
- `is_private_url` returns True on DNS resolution failure but
  uses the same "blocked_target" message — confusing for users
  with transient DNS. Differentiate.
- `recipe-adapter` plugins fire only on CLI `recipe run`. The
  DNA-prompt feedback wiring (v0.1.0a4) is the primary smart-loop;
  adapter plugins are secondary and can be migrated post-launch.
- Top-level `huntova --help` 27-subcommand dump is overwhelming.
  Group help under "Getting started" / "Daily use" / "Outreach".

## Repo / release process

- Pushes go to `enzostrano/huntova-public` ONLY.
- Each release ships `RELEASE-v<version>.md`.
- All 72 tests pass on this release.

## Credits

**Brain:** Enzo (@enzostrano).

**Coding:** Claude (Anthropic), via Claude Code.

Thank you Anthropic.
