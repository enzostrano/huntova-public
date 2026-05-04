# Changelog

## v0.1.0a1120 — 2026-05-04 — public-share /h/<slug> hardening

- Enforce `public_share_enabled` capability flag at the route layer
  (`server.py`). Six share routes now 404 when `HV_PUBLIC_SHARE=0`.
- Retry slug minting on PRIMARY KEY collision in
  `db.create_hunt_share` so a (vanishingly rare) `secrets.token_urlsafe(8)`
  re-roll never bubbles a 500 to the user.
- Stop bumping `view_count` from `/h/<slug>/og.svg` and `/h/<slug>.json`
  — Slack/Twitter unfurls and `huntova hunt --from-share` CLI pulls
  were silently inflating share analytics. Only the HTML page render
  counts now (`db.get_hunt_share` gained a `bump_views` flag).
- Emit `X-Robots-Tag: noindex, nofollow` on `/h/<slug>.json` for
  defence-in-depth (HTML route already had a `<meta robots>` tag).
- Tighten slug shape regex on the OG svg endpoint so malformed
  slugs 404 before touching the database.
- 14 new regression tests in `tests/test_public_share.py`.

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
