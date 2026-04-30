# Changelog

> Per-version release notes also live in `RELEASE-v<version>.md` files
> at the repo root. CHANGELOG is the human-readable summary.

## [0.1.0a10] — 2026-04-30 (eighth drop) — round-9 audit fixes + huntova install-completion

### Added
- **`huntova install-completion`** — one-command shell completion
  install (zsh / bash / fish auto-detect from `$SHELL`). Idempotent
  rc-patching, `--uninstall` reverses cleanly, `--dry-run` previews.
  The legacy `huntova completion <shell>` (prints to stdout) still
  works unchanged.

### Fixed (round-9 audit on v0.1.0a9)
- **HIGH — `huntova logs daemon` dedupe regression.** v0.1.0a9 used
  a tail-relative line index that re-printed lines as the file grew.
  Now uses byte-offset from start-of-file (truly stable) and tracks
  `_DAEMON_LAST_POS` per file across follow-mode polls so only newly
  appended bytes get emitted.
- **HIGH — Grouped `--help` silently swallowed formatter errors.**
  `except Exception: pass` was hiding bugs in `_HELP_CATEGORIES`.
  Now logs to stderr before falling back to the default formatter.
- **MED — `_approx_tokens` provider-blind.** Under-counted Anthropic
  by ~21% and Gemini by ~14%, distorting benchmark cost-est. Now
  uses per-provider chars/token divisor (Claude 3.3, Gemini 3.5,
  OpenAI 4.0).
- **MED — Benchmark score-parse accepted JSON-but-no-scores garbage.**
  Stray `{...}` in chatty Anthropic responses parsed as a dict with
  no expected keys, silently producing all-zeros and skewing
  score-stability. Now requires ≥3 of 5 score keys.
- **MED — `_toml_key` didn't escape control chars.** `\b`, `\f`,
  `\n`, `\r`, NUL emitted raw inside the quoted string crashed
  `tomllib` on re-import. Full TOML basic-string escape set now.
- **LOW — Other `args.force` sites still vulnerable to AttributeError.**
  Applied `getattr(args, "force", False)` prophylactically at 3
  more sites (`plugins install`, `plugins create`, `recipe save`).

### All 72 tests pass.

## [0.1.0a9] — 2026-04-30 (seventh drop) — round-8 audit fixes + huntova benchmark + grouped --help

### Added
- **`huntova benchmark`** — synthetic 3-fixture hunt against every
  configured provider; records score-mean / score-stability / latency
  p50-p90 / estimated cost. `run [--provider P]`, `compare`,
  `fixtures`. Pattern from `openclaw bench`.
- **Top-level `--help` grouped by category** — Getting started /
  Daily use / Outreach / Plugins / Daemon ops / Utility instead of
  the alphabetical 30-command dump.
- **`_toml_key()` helper** — quotes any key containing chars outside
  `[A-Za-z0-9_-]` to prevent TOML round-trip corruption on dotted
  keys.

### Fixed (round-8 audit on v0.1.0a8)
- **CRIT — `cmd_recipe_import` AttributeError on first-time imports.**
  `args.force` was read inside the new coroutine without `getattr`
  defaulting; recipients running `huntova recipe import shared.toml`
  hit a hard crash. Fix: `getattr(args, "force", False)`.
- **CRIT — `_toml_dump_section` emitted invalid TOML on dotted keys.**
  A wizard key like `"domain.tld"` was written as `domain.tld = ...`
  which TOML parses as nested table key, not string. Re-import either
  errored or silently produced wrong shape. Fix via `_toml_key()`.
- **HIGH — `huntova logs --follow` dedupe set unbounded.** Long
  follow sessions accumulated tuples without eviction. Bounded to
  5000 most-recent via `collections.deque` + set, FIFO evict.
- **MED — Daemon follow collapsed identical recurring lines.**
  `_load_daemon` emits `ts=f"{file}:{lineno}"` so each physical line
  stays distinct in the dedupe key.

### All 72 tests pass.

## [0.1.0a8] — 2026-04-30 (sixth drop) — round-7 audit fixes + huntova logs

### Added
- **`huntova logs`** — unified log viewer across agent_runs +
  agent_run_logs + lead_actions + daemon files. `tail [--follow
  --since 1h]`, `hunt <run_id>`, `daemon`, `filter --level`. All
  support `--json`.
- **Onboard end-of-wizard cheat sheet** — both rich (`_onboard_v2`)
  and fallback (`_onboard_v1`) endings show 8 commands so new users
  discover the surface they have, not just `huntova serve`.
- **`classify_url()`** — returns `"ok" / "private" / "unresolvable"
  / "malformed"` so SSRF-gated callers can give better error
  messages. `is_private_url()` is a backwards-compat wrapper.
- **Keychain warning sentinel** — fires once per machine instead of
  on every CLI invocation. Sentinel at
  `~/.config/huntova/.keychain_warned`.

### Fixed (round-7 audit on v0.1.0a7)
- **HIGH — `_toml_dump_section` dropped nested wizard dicts.**
  `normalized_hunt_profile` and `training_dossier` vanished from
  exported TOML; recipient's hunt got garbage results. Fix: emit
  `[name.key]` sub-tables.
- **HIGH — `cmd_recipe_import` 4 separate asyncio loops + no
  rollback.** Failure between merge_settings and save_hunt_recipe
  left the wizard mutated with no recipe row. Fix: single
  `_run_import()` async coroutine; partial failures explicitly
  flagged with remediation hint.
- **HIGH — `_is_secret_key` missed `apikey` (no underscore).** Added
  `apikey / credential / auth / pwd` to substring hints + `endswith
  ("key")` so camelCase doesn't slip through.
- **MED — `_toml_value(None)` encoded as `""`.** Re-import turned
  unset fields into deliberate empty strings, breaking idempotency.
- **MED — Empty `[scoring_rules]` couldn't be imported.** Now
  presence-checked, so a "clean slate" recipe imports correctly.
- **LOW — Post-batch `_check_budget()` re-fired after in-batch
  trigger.** Caused duplicate "stopped" SSE events. Skip when
  `_stop_reason` already set.

### All 72 tests pass.

## [0.1.0a7] — 2026-04-30 (fifth drop) — hunt-timeout-mid-query closed + recipe export/import

### Added
- **Hunt-timeout fires mid-query.** Long-standing known bug from
  v0.1.0a4. Three new `_check_budget()` probes in app.py: per-URL
  inside the main agent loop, per-sub-page inside `deep_qualify`,
  and a post-batch guard. Both `max_leads` and `timeout` caps now
  fire within ~30s of the user-set deadline (was: waited for the
  next iteration boundary, which on a 10-min Playwright deep-qualify
  pass meant the cap couldn't fire until that URL finished).
- **`huntova recipe export / import / diff`** — share hunt configs
  via portable TOML. Auto-strips secrets. `merge_settings` for
  row-locked import. `diff` shows +/- per-key changes before import.

### Fixed
- **CRIT — Mobile-nav CLI link hidden by accident.** v0.1.0a6
  `.n-cli-link {display:none}` had specificity that beat the mobile
  media-query, so the link disappeared on mobile too. Wrapped in
  `@media(min-width:901px)`.
- **HIGH — `huntova approve --top N` spawned 2N asyncio loops.**
  Same anti-pattern v0.1.0a6 fixed in `cli_migrate`. Now wraps
  bulk-approve in a single `_run_bulk()` async coroutine.
- **MED — `/favicon.ico` 500 on missing file + no cache.** Returns
  204 if file missing (avoids leaking server path); adds
  `Cache-Control: public, max-age=86400`.
- **MED — CSP missing `frame-ancestors 'none'`.** Added to global
  middleware. Closes clickjacking surface.
- **LOW — `tests/test_providers.py` regex stale.** Updated to match
  the v0.1.0a3 error message rewrite. All 72 tests pass.

## [0.1.0a6] — 2026-04-30 (fourth drop) — silent-failure killers + huntova approve

### Added
- **`huntova approve`** — manual review queue for high-fit leads
  before the agent emails them. `queue` (table sorted by fit_score),
  `<id>` (approve), `--top N` (bulk-approve), `--reject <id>`,
  `diff <id>` (side-by-side AI draft vs source evidence).
- **`/favicon.ico` route** — was 404 on every page load. Blanket fix
  serves `static/favicon-32x32.png` site-wide.

### Fixed (critical)
- **CRIT — `HV_SECRET_KEY` autogen silent failure.** v0.1.0a5
  swallowed keychain write errors with `except Exception: pass`,
  meaning the env var was never set and the next CLI run regenerated
  a fresh key, **invalidating every signed session/cookie**. Fix:
  always set `os.environ` first, surface the keychain failure to
  stderr.
- **HIGH — Rate-limit dict memory leak.** v0.1.0a5's
  `_check_test_endpoint_rate` was missing the 5-min GC sweep that
  `_check_export_rate` already had. Long-running deploys would creep
  until OOM-kill.
- **HIGH — `huntova migrate` OOM + asyncio churn.** Dry-run preview
  was slurping the whole CSV (500MB Apollo export → CLI OOM); import
  loop spawned a fresh asyncio loop + DB-pool checkout per row (50k
  loops on 50k rows, starving concurrent agent threads). Fix:
  streaming preview + single asyncio loop wrapping the import.
- **MED — SMTP test oracle re-leaked via error message.**
  `str(e)[:120]` echoed "Connection refused" / "timed out" to the
  client, defeating the port-allowlist + private-IP gate the v0.1.0a5
  fix relied on. Fix: single canonical "SMTP connection failed"
  message.

### Fixed (cosmetic)
- `/setup` API-key field wrapped in `<form>` for password-manager
  compliance.
- Landing nav `CLI` link hidden on desktop (kept in mobile drawer +
  footer).

## [0.1.0a5] — 2026-04-30 (third drop) — security hardening + huntova migrate

### Added
- **`huntova migrate`** — bulk import from Apollo / Clay / Hunter /
  generic CSV. Predefined column maps for the 3 main competitors;
  auto-detect heuristic for generic CSV. `--map csv_col=lead_field`
  override, `--dry-run` / `stats` for inspection without writing.
  BOM-tolerant (Excel/Sheets exports work). Idempotent dedup.
- **`huntova --version`** flag (was throwing "unrecognized arguments").
- **First-run polish:** onboard auto-generates `HV_SECRET_KEY` and
  saves to keychain; tightens `db.sqlite` perms to 0600. Both used to
  fire SEV-2 in `huntova security audit` immediately on a fresh install.
- `_hydrate_env_from_local_config` now round-trips `HV_SECRET_KEY` so
  the dev-fallback warning doesn't keep firing.

### Fixed (security)
- **SEV-1 — SSRF in `/api/webhooks/test`.** Logged-in user could save
  `webhook_url=http://169.254.169.254/...` and exfiltrate cloud-instance
  metadata via the test endpoint. Now gated by `app.is_private_url()`.
- **SEV-2 — SMTP test port-scan oracle.** Restricted to {25, 465, 587,
  2525} + private-IP gate.
- **SEV-2 — Test endpoints rate-unlimited.** Added 5/minute per
  (user, endpoint) limiter.
- **SEV-2 — `/api/settings` GET leaked secrets.** Strips
  smtp_password / webhook_secret / plugin_slack_webhook_url before
  returning. Defends against legacy DB rows.
- **SEV-2 — `/api/account/export` missing one secret in strip list.**
  Added `plugin_slack_webhook_url`.

### Fixed (UX)
- `--reset-scope keys` now also pops env vars from `os.environ`.
- Chat `start_hunt` action forwards AI-supplied `timeout_minutes`.
- Test suite default flipped to Anthropic.

## [0.1.0a4] — 2026-04-30 (later) — adaptive smart-loop + 8 launch-blocker fixes

### Added
- **Real adaptive learning.** `app.py` Stage-1 + Stage-2 DNA prompts
  now read `_feedback_good` / `_feedback_bad` lists and inject
  POSITIVE PATTERNS / AVOID PATTERNS sections. Click Good Fit / Bad
  Fit on leads → next hunt's queries + scoring shift toward what
  you said was good. DNA payload exposes `_adapted_from_feedback`
  so probes can verify the loop fired.
- **`huntova memory`** — `search` (fuzzy across saved leads),
  `inspect <id>` (full lead dump), `recent [--days N]`, `stats`.
  New `cli_memory.py` module.
- **OpenClaw-style banner** — randomized witty tagline rotation
  (10 entries), boxed "Existing config detected" card on returning
  onboard runs.
- **Settings UI 5-tab modal** in dashboard — Plugins toggles,
  Webhooks (URL + secret + Test button), SMTP outreach,
  Preferences (theme/reduced-motion/telemetry), Account/Data
  (export JSON bundle, GDPR erasure).

### Fixed
- Anthropic SDK shipped in base deps (was optional). Default-provider
  happy path was crashing with `RuntimeError: anthropic SDK not installed`.
- Chat REPL multi-turn on Anthropic (prefill `{` was creating two
  adjacent assistant turns; Anthropic API rejected). Now strips
  trailing assistant before prefill.
- `/setup` web wizard correctly tags Anthropic as "Default" (was
  still tagging Gemini).
- Telemetry `cli_init` event provider field flipped to `anthropic`.
- "no API key" error hint now leads with `HV_ANTHROPIC_KEY`.
- `install.sh`: `set -o pipefail` so playwright crashes don't hide;
  removed misleading `| grep` pipe; LC_ALL/LANG default to C.UTF-8
  so the ASCII logo + 🦊 emoji don't garble on bare cloud-init shells.

### Notes
- Default provider switched to Anthropic Claude (was Gemini). Huntova
  was built using Claude end-to-end.
- README "Credits" section: brain by @enzostrano, coding by Claude.
- Push policy: public repo only (`enzostrano/huntova-public`).
  Each release ships `RELEASE-v<version>.md` with updates / fixes /
  known bugs.

## [0.1.0a3] — 2026-04-30 — single-command install + chat + Anthropic default

### Added
- `huntova chat` REPL (natural language → JSON action dispatch).
- `huntova security audit` (10 local checks).
- `huntova config unset` / `validate`.
- 13 per-provider `--*-api-key` flags on onboard.
- `--reset-scope {config,keys,full}` / `--flow` / `--mode` /
  `--accept-risk` / `--json` on onboard.
- ASCII-logo banner + chat-style banter in `install.sh`.
- Single-command install: `curl -fsSL huntova.com/install.sh | sh`.

### Fixed
- `huntova onboard --browser` NameError crash.
- Per-provider model selection (Anthropic/OpenAI/Ollama users were
  getting Gemini model IDs passed to wrong SDK).
- Local-mode gear icon clobbered by `hvLoadAccount`.
- `huntova config unset` corrupting multi-line TOML arrays.
- pip 21.2 / Python 3.9 install friction.

### Notes
- Default provider switched: Gemini → Anthropic Claude.

## [0.1.0a2] — 2026-04-30 launch-prep polish

The OpenClaw-equivalence push. Brought the install / onboarding / web-
wizard / daemon-install experience to parity with the polish bar OpenClaw
set, while keeping the lead-gen-specific surface (proof packs, Agent DNA,
recipe adaptation, share links) firmly Huntova-shaped.

### Added

- **TUI onboarding wizard** at `tui.py` — questionary-backed three-phase
  flow (filesystem → provider/key → launch), banner, spinner, browser
  detection (SSH / WSL / DISPLAY-aware), graceful Ctrl+C handling.
- **Web setup wizard** at `/setup` — three tabs (9 cloud providers / 3
  local AI servers / custom OpenAI-compatible endpoint), live "● detected"
  badge for running localhost servers, save-to-keychain via `/api/setup/key`.
- **Daemon installer** at `huntova_daemon.py` — launchd LaunchAgent on
  macOS, systemd `--user` unit on Linux. `huntova daemon install / start
  / stop / status / logs / uninstall`.
- **13 AI providers** supported through a unified `_GenericOpenAICompat`
  abstraction: Gemini, Anthropic, OpenAI, OpenRouter, Groq, DeepSeek,
  Together, Mistral, Perplexity, Ollama, LM Studio, llamafile, custom
  endpoint. Auto-detection of running local servers via
  `providers.detect_local_servers()`.
- **Operational dashboard** — `huntova status` renders daemon / server /
  providers / plugins / data / last hunt as a one-screen table.
- **Config CLI** — `huntova config show / get / set / edit / path`.
  Refuses to set anything that looks like a secret (those go to keychain).
- **Integration test command** — `huntova test-integrations` probes
  AI / SearXNG / Playwright / plugins / SMTP and reports skipped
  vs failed vs passed. Exits 0 on a clean install with no providers
  configured (skipped ≠ failed).
- **Public plugin browse page** at `/plugins` — capability-disclosing
  cards (network / secrets / filesystem_write / subprocess), filter
  by capability, copy-to-clipboard install command per row.
- **Compare pages** at `/compare/{clay,apollo,hunter}` — captures search
  traffic for "X alternative" queries with side-by-side feature tables.
- **Dynamic OG SVG images** at `/h/<slug>/og.svg` — terminal-styled
  1200x630 social previews showing the actual hunt query + top 3 leads
  + fit scores. PREVIEW MODE pill on /try-minted shares.
- **CONTRIBUTING.md + GitHub issue/PR templates + docs/PLUGINS.md** —
  public-repo presentation.
- **NOTICE.md** — MIT attribution to OpenClaw for the patterns adapted
  (TUI shape, daemon installer, browser-launch logic, install-script
  shape). No OpenClaw code reproduced verbatim; all reimplementations
  are Python-original.

### Fixed (security + correctness from code-review sweep)

- `providers.py:_build` now accepts settings — custom provider no longer
  hits NameError on first use.
- `cli.py:_hydrate_env_from_local_config` restores all 13 provider keys
  from keychain (was 3) plus HV_CUSTOM_BASE_URL/MODEL.
- `server.py:/api/setup/key` fails closed when runtime module can't
  import (was: silently bypassed cloud-mode gate).
- `server.py` admin endpoints use `hmac.compare_digest` (was: `==`,
  timing-attack vulnerable).
- `huntova_daemon.py` systemd `Environment=` values escape backslash,
  double-quote, and dollar.
- `cli.py:cmd_status` no longer sets `socket.setdefaulttimeout` (was
  process-global, leaked across commands).
- `server.py:/h/<slug>/og.svg` validates slug regex before DB lookup.
- `cli.py:_onboard_v1` polls server readiness instead of fixed sleep;
  cleans up spawned subprocess on Ctrl+C.
- `plugins.py` `post_score` / `pre_draft` / `post_draft` no longer
  fire twice per call — adaptation deltas are no longer doubled.

### Fixed (UX + visual polish)

- `huntova plugins ls` accepts the alias (was: invalid choice).
- `huntova config show` exits 0 on missing config (was: 1).
- All "huntova init" recommendations replaced with "huntova onboard".
- `huntova share <slug>` works as `share status <slug>` (argparse no
  longer rejects the bare slug).
- /landing CTA gradient + link hover were fading to invisible 8% alpha
  purple; now use solid `--pur` tone.
- /landing hero card "Verified lead" label looked fake; relabelled
  "Sample lead" / "Example output".
- /landing footer: removed unclaimed instagram.com/huntova.ai +
  x.com/huntova_ai links. Added /demo /download /plugins /try links to
  the right columns.
- Dashboard topnav "0 credits / 0 leads left" hidden until /api/account
  loads (was: showed "0" briefly on every refresh).
- /privacy + /terms + /reset palette unified with the rest of the site
  (cyan #36dfc4 → purple #7c5cff).
- /plugins inherits Satoshi from the share-shell (was: local Inter
  override). All share-shell pages (/demo, /h, /compare, /plugins,
  /try) now load Satoshi from Fontshare.
- /demo banner reads "Sample Proof Pack — illustrative output" (was:
  same "Preview-generated sample" copy as /try-minted shares).
- CLI output is clean by default — SECRET_KEY warning, DB init log,
  and auth bootstrap log all gated behind `HV_VERBOSE_LOGS`.

### Changed

- `pyproject.toml` version 0.1.0a1 → 0.1.0a2; URLs point to
  `enzostrano/huntova-public` (the public source repo, not the private
  development repo).
- README rewritten for the v0.1.0a2 surface — new install command,
  full command list (status / config / daemon / examples / metrics),
  plugin count corrected to 5 (was 3).

## [Unreleased] — 2026-04-30 BYOK pivot

The hosted-SaaS-only codebase pivoted to a downloadable CLI tool. Cloud
beta still works unchanged; local CLI mode is now first-class.

### Added

- `huntova` CLI entry point with `serve` / `init` / `doctor` / `update` / `version` subcommands (`cli.py`).
- BYOK provider abstraction supporting Google Gemini, Anthropic Claude, and OpenAI (`providers.py`).
- Local secret storage via OS keychain → Fernet-encrypted file → 0600 plaintext fallback (`secrets_store.py`).
- SQLite backend behind `APP_MODE=local`; PostgreSQL stays for cloud (`db_driver.py`).
- `RuntimeCapabilities` flag system for cloud-vs-local feature gating (`runtime.py`).
- `BillingPolicy` short-circuits credit/tier/Stripe gates in local mode (`policy.py`).
- Single-user auto-login bootstrap in local mode — no signup, no cookies (`auth.py:_ensure_local_user`).
- `pyproject.toml` for `pipx install huntova`.
- `/download` marketing page modelled on openclaw.ai with three install paths.
- `/install.sh` curl-pipe installer that auto-installs pipx if missing.
- `tools/smoke_test_local.py` — 25-check smoke test for local-mode boot.
- `LICENSE` (AGPL-3.0-or-later).
- `README.md` rewrite covering CLI install / config / providers.

### Changed

- `db.py` routes all queries through the driver shim so the same SQL works against PostgreSQL or SQLite.
- `auth.py:require_feature` and `user_has_feature` delegate to `policy.feature_allowed` (BYOK users get every feature unlocked).
- `agent_runner.start_agent` skips credit precheck when `policy.deduct_on_save()` is False.
- `server.py:/api/checkout` returns 503 in local mode; `/api/webhook/stripe` returns 200 + ignored.
- All `client.chat.completions.create(...)` call sites now flow through `providers.chat_compat()` so the user's selected BYOK provider handles every AI call.
- `config.py:AI_PROVIDER` recognises `gemini` / `openai` / `anthropic` / `lm-studio`.
- `SEARXNG_URL` defaults to `https://searx.be` in local mode (cloud keeps the Railway sidecar default).
- Frontend hides the credit pill, pricing modal, account-page upgrade buttons, plan card, and Log out button when `billing_enabled=false` / `auth_enabled=false`.

### Removed

- Nothing yet — the pivot is additive. Stripe / credit_ledger / tier columns are still in the schema for cloud compatibility; they'll be dropped in a future cleanup commit once cloud is sunset.

### Migration notes for testers on the cloud beta

- Cloud installs keep working. `APP_MODE=cloud` is the default.
- Anyone who wants the local CLI can `pip install -e .` from the repo today and run `huntova init` + `huntova serve`. PyPI publish is pending a workflow setup.

### Known follow-ups

- PyPI publish: GitHub Actions workflows drafted at `docs/workflows/`. Add them to `.github/workflows/` via the GitHub UI (the local Git client lacks the `workflow` OAuth scope).
- Single-file binary distribution (PyInstaller / PyOxidizer) for non-technical users who don't have Python.
- `huntova hunt --topic "..."` headless one-shot CLI subcommand.
- Admin panel Billing / Stripe tabs deletion (cloud-only, hidden in local).

### Sprint addenda (commits 50–52) — round-69 brainstorm execution

3 Perplexity tabs (GPT-5.4 / Gemini 3.1 Pro Thinking / Kimi K2.6) were briefed in round 69 on:
- v2.0 spike (Tab 0): Outcome-trained recipe DNA — recipes that learn from their own outcomes
- Launch playbook (Tab 1): Hour-by-hour Show HN strategy
- Community plugin strategy (Tab 2): GitHub static registry + capabilities disclosure

Shipped:
- `9dbe563` — Plugin Capabilities Disclosure (Tab 2). Plugin Protocol declares optional `capabilities: list[str]` (network / secrets / filesystem_write / subprocess). 3 bundled plugins annotated. Registry rewritten with Kimi's commission shortlist (notion / apollo / webhook / emailguard / hiring-signal). `huntova plugins search` shows verified ✓ vs community ○ badges + capability chips.
- `d3abab2` — `huntova recipe inspect <name>` (Tab 0 step 1 of v2.0 DNA). Read-only outcome aggregator: feedback (good/bad/none), fit bands, email_status histogram, sent/replied counts, reply rate %, plus a 4-rule heuristic "signals" hint (★ tuned / ! weak fit / ★ strong reply rate / ! low reply rate). Also fixed `idx_lead_feedback_user_lead` UNIQUE INDEX missing in SQLite (migration was cloud-only).
- `89079ca` — `huntova recipe adapt <name>` (Tab 0 step 2 of v2.0 DNA). Sends recipe's outcome corpus to the configured AI provider (Gemini/Anthropic/OpenAI via providers.chat_compat) with a JSON-only system prompt. Returns structured adaptation card (overperforming_patterns / weak_patterns / winning_query_terms / suppress_terms / recommended_query_additions / reply_correlated_signals / summary). Persisted as `adaptation_json` on the recipe row. `huntova recipe inspect` now surfaces the card if present. First place Huntova does AI inference outside the agent loop — validates BYOK end-to-end.

### Sprint addenda (commits 47–49) — round-68 brainstorm execution

3 Perplexity tabs in round 68 brainstormed:
- Reachability waterfall design (Tab 0): 5-tier ladder + 3-bullet reasons + proof trail
- Conversion-optimised share UX + Hosted Cloud Proxy paid wedge (Tab 1)
- 3 reference plugins to ship in the wheel (Tab 2)

Shipped:
- `5055801` — `/demo` page (live-rendered Proof Pack with 4 sample leads across all 5 reachability tiers) + reachability v2 (Tab 0's additive 0-100 weight model with 5 named tiers, 3 reasons per lead, and a "proof trail" pill row).
- `bf52228` — `/compare/clay`, `/compare/apollo`, `/compare/hunter` side-by-side comparison pages catching "X alternative" search traffic. Mobile-responsive comparison table with verdict + dual CTAs.
- `b4645fc` — 3 bundled reference plugins per Tab 2 ranking: csv-sink (post_save → local CSV), dedup-by-domain (post_search → JSONL rolling-window state), slack-ping (post_save → Slack webhook). Auto-loaded via `register_bundled`. `HV_DISABLE_BUNDLED_PLUGINS=1` to opt out. 6 new pytest tests covering discovery, opt-out, no-op-without-config, intra-batch dedup, slack silent-on-missing-webhook.

### Sprint addenda (commits 36–46) — round-67 strategic brainstorm execution

Three Perplexity threads (GPT-5.4 Thinking / Gemini 3.1 Pro Thinking / Kimi K2.6 Thinking) brainstormed Huntova's positioning + growth + architecture in parallel. The synthesis turned into 6 commits:

- `4d0f77b` — bash/zsh/fish shell completion via `huntova completion <shell>`. Static scripts, no extra dep.
- `bda55ff` — CHANGELOG addenda for the prior CLI command sweep.
- `379f349` — **Proof Pack** rendering on `/h/<slug>` (quoted evidence + source chips + freshness + verified status). **Viral share page**: blurred bottom half of leads + inline unlock CTA + sticky bottom bar with `$ _ Generated locally with Huntova CLI` and a 1-click `pipx install huntova` copy button. Tab 0's "Clay gives fields, Huntova gives proof" + Tab 1's growth loop, shipped together.
- `0bd931b` — `huntova hunt --from-share <slug>` closes the growth loop. Fetches `/h/<slug>.json`, adopts the original country set, runs a fresh hunt locally. The unlock-CTA on the share page is now a real, working command.
- `d30bc83` — **Plugin protocol** + PluginRegistry (Tab 2 / Kimi spec). 8 lifecycle hooks (pre_search, post_search, pre_score, post_score, post_qualify, post_save, pre_draft, post_draft). Dual discovery: `entry_points("huntova.plugins")` + `~/.config/huntova/plugins/*.py`. Idempotent registration, error-isolated dispatch, `huntova plugins` listing subcommand. 6 new pytest tests.
- `355174b` — wire `pre_search` + `post_save` hooks into the actual agent loop in app.py, plus `huntova plugins create <name>` scaffolding. Plugins now ship value, not just types.

**13 user-facing CLI subcommands.** 48 pytest + 25 smoke = 73 verification points.

### Sprint addenda (commits 33–35)

- `10eae27` — pytest coverage for cmd_lead, cmd_share, ls --filter (5 new tests).
- `25a2a7e` — `huntova rm <id>` for CLI lead deletion. Confirmation prompt by default, `--yes` to skip. Cascades to lead_feedback + lead_actions via existing db.permanent_delete_lead.
- `9419efa` — `huntova history` lists recent agent runs from agent_runs (id, status, leads_found, queries done/total, started_at). Color-coded status. `--limit N --format json` for piping. Reads through the db_driver shim so cloud + local backends both work.

**Total CLI surface: 11 user-facing subcommands** — serve, hunt, ls, lead, rm, history, export, share, init, doctor, update, version.

### Sprint addenda (commits 28–32)

- `c7097b1` — README + CHANGELOG updated to cover ls/export/share/doctor probe.
- `a20c306` — `/h/<slug>` share-page CTAs route to `/download` in local mode (CLI install instructions, not cloud signup).
- `fb8a284` — `huntova hunt --json` (JSONL stream for shell pipes) + `--dry-run` (walks setup, doesn't burn AI calls). Routes `[WARNING]` and `[DB]` prints to stderr so stdout stays pure JSONL.
- `e5b6a20` — `huntova lead <id>` for full-detail single-lead view. `--by-org` for partial name match, `--first` for disambiguating, `--format json` for piping. Bold + dim ANSI styling on TTY.
- `f08d95f` — `huntova ls --filter` with two modes: substring scan across common text fields, or `field:value` exact match. README + /download FAQ updated.

### Sprint addenda (commits 26–27)

- `e921d16` — surface the full CLI command list in the /download FAQ.
- `97dd6f1` — `huntova doctor` now sends a live 5-token "respond with OK" round-trip to the configured provider so users can confirm their key actually works (not just that the env var is set). `--quick` skips for CI.

### Sprint addenda (commits 24–25)

- `7a0ecaf` — link from `/landing` to `/download` so existing cloud users discover the CLI.
- `31832e0` — `huntova share --top N --title "..."` creates a public `/h/<slug>` URL from terminal. Reuses server.py's _SHARE_LEAD_FIELDS whitelist so CLI + cloud snapshot the same public-safe fields. End-to-end verified.

### Sprint addenda (commits 19–23)

- `703cb14` — extend `.gitignore` for `*.egg-info`, build artifacts, and `*.sqlite-*` files.
- `c48ca34` — `huntova hunt` headless CLI subcommand. Runs the agent in-process, subscribes to the user's UserEventBus, streams formatted leads to stdout. `--countries`, `--max-leads`, `--verbose` flags. Top-5 summary on completion.
- `579a78e` — README + `/download` page surface `huntova hunt` example output (terminal block with green checkmarks, dimmed prompt). Discoverable for first-time visitors.
- `8c58dae` — `huntova ls` (table or json, ANSI-coloured fit chips) and `huntova export` (CSV or JSON to stdout). Both share a `_bootstrap_local_env()` preflight with `huntova hunt`.
- `f9529f0` — pytest suite: 23 tests covering runtime, db_driver SQL translation, policy, providers (default, preferred, fallback, error), secrets_store (set/get/delete + plaintext fallback). All green in 0.5s.

### Sprint addenda (commits 14–17)

- `9ae8e55` — CHANGELOG.md + GitHub Actions workflow drafts at `docs/workflows/`.
- `f2409f1` — fix: pyproject.toml py-modules missing providers / secrets_store / policy / db_driver, so freshly-installed CLIs failed to import them. Moved keyring + cryptography into the base install dependencies (was optional). New `[anthropic]` extra for the Claude SDK.
- `e00c6ef` — `huntova doctor` now probes SEARXNG_URL with a real format=json request and reports ✓ / ⚠ / ✗ with actionable next-steps. README adds a "Self-host SearXNG" section with the Docker one-liner.
- `333005c` — DuckDuckGo HTML fallback in app.py:search(). When SearXNG returns 0 results, times out, or is unreachable, _ddg_fallback_search() scrapes the no-JS endpoint at html.duckduckgo.com, parses results via regex, resolves DDG redirect URLs, and returns SearchResult instances. Live-tested against real queries — returns 5+ results out of the box. First-time testers now get search working without any infrastructure setup.
