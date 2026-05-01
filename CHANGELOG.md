# Changelog

All releases are tagged on [enzostrano/huntova-public](https://github.com/enzostrano/huntova-public/releases). This file is the durable, human-readable summary; per-release detail (a single `RELEASE-v<version>.md` written transiently for the GitHub release body) is not committed.

Versioning: `0.1.0aNN` alpha increments. Public install path is unchanged across all alphas (`pipx install huntova` or `curl -fsSL https://huntova.com/install.sh | sh`).

---

## 0.1.0a110 → 0.1.0a132 — May 1 2026 — autonomous local-first cold-email stack

23-release sweep that takes Huntova from "lead-gen agent" to a full
local-first replacement for Apollo / Clay / Hunter / Instantly /
Lemlist / Mailmeteor / GlockApps / Mailshake.

### New subcommands

- **`huntova quickstart`** (a132) — single-command interactive
  walkthrough: pick playbook → run first hunt → preview drafts →
  next-step list. Sub-30-second time-to-first-lead for new
  installs.
- **`huntova research <id>` / `--batch N --above SCORE`** (a116, a123)
  — deep-research one or N leads. 14-page crawl per lead, AI
  rewrites the opener with a hook the prospect would recognise.
- **`huntova sequence run / status / pause`** (a113) — 3-step
  follow-up cadence (Day +4 bump, Day +9 final). Auto-enrolled by
  `huntova outreach send`, auto-paused on reply detection.
- **`huntova inbox setup / check / watch`** (a112) — IMAP poll,
  matches incoming `In-Reply-To` to outbound `_message_id`s, flips
  matched leads to `email_status=replied`. Now also AI-classifies
  every reply (a120) into `interested / not_now / not_interested /
  out_of_office / wrong_person / unsubscribe` and routes status
  + cadence + DNA feedback accordingly.
- **`huntova doctor --email`** (a114) — SPF / DKIM / DMARC / MX
  pre-flight; `dnspython` is now a base dep (a124).
- **`huntova pulse [--since 7d]`** (a122) — weekly self-coaching
  summary with concrete next-action recommendations.
- **`huntova schedule print --target launchd|systemd|cron`** (a128)
  — emits OS-native scheduled-job config for daily auto-runs of
  sequence + inbox + pulse. Doesn't auto-install (no silent
  persistence) — user pipes the snippet into the right place.
- **`huntova playbook` / `huntova playbooks`** (a127) — friendlier
  alias for `huntova examples`.

### Existing surfaces extended

- **Chat as brain** (a111, a125, a126) — both `/api/chat` (web) and
  `huntova chat` (CLI) now drive the entire stack via plain English:
  `update_settings`, `update_icp`, `set_lead_status`, `delete_lead`,
  `mint_share`, `research`, `sequence_run`, `sequence_status`,
  `inbox_check`, `pulse`, `playbook_install`, `playbook_ls`,
  `navigate`, `start_hunt`, `list_leads`, `answer`. The model
  receives live state in the system prompt so it doesn't have to
  guess current settings.
- **`huntova outreach send --research-above SCORE`** (a118) —
  auto-runs deep-research on every lead whose `fit_score` crosses
  the threshold *before* sending the opener. The previous draft
  archives into `rewrite_history` for one-click revert.
- **`huntova ls`** (a124) — three new shortcuts: `--status`,
  `--reply-class`, `--min-fit`. Combinable.
- **`huntova status`** (a130) — three new rows: sequence step
  counts, IMAP configured Y/N, daily-schedule installed Y/N. When
  missing, prints the exact subcommand to fix.
- **10 bundled playbooks** (a115) — `agencies-eu`,
  `b2b-saas-hiring`, `tech-recruiting`, `ecommerce-shopify`,
  `solo-coach`, `consultant-fractional`, `video-production`,
  `saas-developer-tools`, `design-studio`, `podcast-producer`.
  Install auto-seeds wizard `business_description`, `target_clients`,
  and `default_tone`.

### Repo housekeeping

- a110 deleted 103 stale `RELEASE-v0.1.0a*.md` files. CHANGELOG.md
  is the durable record; per-release notes are written transiently
  for the GitHub release body and gitignored.
- Added `PRIVACY.md` and `TERMS.md`.
- Removed dev-only planning files (`NEXT_PHASE_PLAN.md`,
  `REDESIGN.md`, `ROADMAP.md`, `STRATEGY.md`,
  `STABILITY-SWEEP-2026-04-30.md`, `CHECKPOINT.md`).
- Purged 40 MB+ of build artefacts and a stale orphan
  `frontend/node_modules/`.

### Bug fixes

- a131: Message-ID strip uses `.strip("<>")` (substring) at three
  sites — `lstrip("<").rstrip(">")` was a character-set strip that
  could mangle nested-bracket IDs from some ESPs.
- Many smaller fixes across the new modules caught by parallel
  bug-hunt agents during the sprint.

---

## 0.1.0a85 → 0.1.0a109 — May 1 2026 — extraction + storage hardening sprint

25-release sweep focused on quality of the data the agent extracts, the integrity of state once stored, and the privacy/UX edges around shared lead pages.

### Extraction & search

- LinkedIn URL regex now matches country-code subdomains (`uk.linkedin.com`, `de.linkedin.com`, …).
- Page-text strip runs `html.unescape()` after tag removal — the AI sees `Acme & Co.` instead of `Acme &amp; Co.`.
- `crawl_prospect._fetch` reads `Content-Type` and bails on PDF / image / video / octet-stream / zip *before* HTML regex (was producing binary garbage that polluted scoring text).
- `_search_session` now sets a real Huntova `User-Agent` + JSON-first `Accept` so SearXNG instances stop 403'ing the requests-bot UA.
- `_hostish_netloc` strips an explicit port (`:443`/`:8080`) before block matching.
- `dedup-by-domain` plugin lowercases the host before the `www.` strip.
- `_NOREPLY_RE` covers `do-not-reply`, `do_not_reply`, `automated`, `auto-reply` (in addition to the existing variants) so those addresses never land in `contact_email`.
- `validate_email` accepts the `Name <email@domain>` capture form so JSON-LD `ContactPoint` blocks yield real emails.
- Jina fallback explicitly logs the 429 case so operators can tell why JS-heavy sites stop extracting.

### Scoring quality

- Country case-folded (`"france"` / `"FRANCE"`) before `eu_countries` set lookup.
- Leadership-role regex got `\b…\b` so "directorate" / "CEOgrade" stop matching as the contact role.
- `is_recurring` keyword tuple includes `annual / yearly / weekly / every {year,month,week}`.
- `_clean_subject` strips `Re:` / `Fw:` / `Fwd:` prefixes (cold emails stop looking like replies).
- `BANNED_WORDS` covers the `i hope this finds you` / `hope you're doing well` / `just wanted to` family.
- Strict `_coerce_bool` for `is_recurring` / `is_virtual_only` — string `"false"` no longer coerces to `True`.
- `event_type` deduped against `event_name` when the AI restates the same string for both.
- Free-form summary fields (`why_fit`, `evidence_quote`, `production_gap`) capped at 240 chars so chatty AI responses don't blow out the row layout.
- Team-page role extraction strips leading articles ("the head of sales") and trailing punctuation.

### Storage correctness

- `upsert_lead ON CONFLICT` preserves user-set `email_status` (no more reset to `"new"` on re-discovery) and keeps `GREATEST(fit_score)` (no more downgrade on re-encounter).
- `status_history` skips consecutive duplicates and trims to last 100 (timeline stops becoming wallpaper).
- `_run_log` capped at 1 000 entries (used to grow unbounded across long hunts).

### API & endpoints

- `/h/<slug>` share pages set `Cache-Control: private, no-cache, no-store, must-revalidate` + `X-Robots-Tag: noindex, nofollow`.
- `/api/wizard/scan` sets full no-cache / private headers.
- `/api/update` 404 returns canonical `{"ok": false, "error": "not found"}` shape.
- `/api/update` caps `notes` at 4 000 chars.
- CSV export ships UTF-8 BOM + `charset=utf-8` (Excel mojibake on accented org names fixed).
- `/api/export/json` adds `default=str` so a stray `datetime`/`Decimal` no longer crashes export.
- `/api/export/account-data` switched to UTC for `exported_at` and the filename day component.
- `/api/rewrite` whitelists `tone` against `friendly/consultative/broadcast/warm/formal`.
- `_all_emails_found` excludes the chosen `contact_email` (no more accidental double-mail).
- Provider-test error message redacts the API key (`***redacted***`) before formatting.

### UI / agent runner

- Email rewrite-history "revert" passes the original-array index (newest-first display, but reverts the version actually clicked).
- Inline edit-save preserves an explicitly-cleared subject/body (no more falling back to old value on `||` short-circuit).
- Wizard `.iwiz-assist-input` font-size 16 px in the `<600px` breakpoint (iOS auto-zoom on focus stopped).
- `stop_agent()` removes the user from the queue so cancelling while queued no longer kicks off the hunt later.

### CLI / packaging

- `huntova rm` returns exit 1 when the user types `n` at the confirm prompt (no more silent "success").
- SMTP `_send_email_sync` branches on port 465 → `SMTP_SSL` (cloud users on 465-only providers can now send mail).
- `huntova migrate` exits 1 on zero imports + writes `[huntova] no valid rows imported` to stderr.
- `huntova benchmark` only targets actually-configured providers (no more bogus auth-failed rows for keys the user never set).
- Landing-page install one-liner uses explicit `https://`.

---

## 0.1.0a30 → 0.1.0a84 — Apr 30 → May 1 2026 — local-first BYOK pivot + agent-driven bug hunt

Pivot from hosted SaaS to local-first BYOK CLI distributed via pipx + GitHub Releases. Capability flags in `runtime.py` collapse billing/auth/SMTP/OAuth in local mode.

Highlights:
- Anthropic Claude becomes the BYOK default provider; 13 providers wired (`providers.py`).
- OS keychain-first secrets storage with encrypted-file fallback (`secrets_store.py`).
- SQLite singleton-conn driver with an application-level `_SqliteSerial` lock (a34) — 100/100 concurrency stress test passes.
- SearXNG decoupled — public instance default for local, sidecar default for cloud.
- Plugins: `csv-sink`, `dedup-by-domain`, `slack-ping`, `generic-webhook`, `recipe-adapter`, `adaptation-rules` ship in the wheel.
- Agent runner: `MAX_CONCURRENT_AGENTS = 1`, per-user queue, terminal-state SSE event on completion.
- Share pages, recipes, daemon installer (launchd / systemd-user), shell-completion installer, memory subcommand, migrate, approve, teach, logs, benchmark.
- Roughly 50 distinct fixes shipped via the agent-driven hunt loop (CSV formula injection defense, SSE bus dead-sub sweep, `_atomic_write_0600`, JSON-LD `@graph` envelope, refreshed-lead dedup, GREATEST→MAX SQLite translation, year regex tightening, …).

## 0.1.0a3 → 0.1.0a29 — Apr 30 → mid-Apr 2026 — TUI, daemon, memory, migrate, approve

Onboard wizard ergonomics, OS-native daemon installer, memory subcommand, CSV migrate from Apollo / Clay / Hunter, manual approve queue.

## 0.1.0a1 → 0.1.0a2 — first public alpha

Initial public release. Local CLI shape, SearXNG-driven hunt, BYOK provider abstraction, FastAPI dashboard.
