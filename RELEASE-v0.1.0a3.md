# Huntova v0.1.0a3 — 2026-04-30

OpenClaw-equivalence push. Bringing the install / onboarding / web-wizard /
daemon-install experience to parity with the polish bar OpenClaw set,
while keeping the lead-gen-specific surface (proof packs, Agent DNA,
recipe adaptation, share links) firmly Huntova-shaped.

## Updates (what shipped)

### Default provider switched: Gemini → Anthropic Claude
- `HV_AI_PROVIDER` default is now `anthropic`. Huntova was built using
  Claude end-to-end and we ship with the model that gave us the best
  agent quality during development. Other providers stay fully
  supported; switch via `huntova onboard` or env var.
- `_DEFAULT_ORDER` in `providers.py` puts `anthropic` first.
- All 13 cloud providers + 3 local AI servers + custom OpenAI-
  compatible endpoint still work unchanged.

### `huntova chat` (new) — natural-language CLI
- REPL: free-text prompt → AI parses to one of three actions:
  `start_hunt {countries, max_leads, timeout_minutes, icp}`,
  `list_leads {filter}`, or `answer {text}`.
- Dispatches in-process to `cmd_hunt` / `cmd_ls` — no subprocess.
- 20-turn rolling history. Recovers from malformed JSON.
- **Anthropic JSON-mode trick**: Claude has no `response_format=json
  _object`. We prefill the assistant turn with `{` and concatenate
  the leading brace to the response. Other providers use their
  native JSON mode unchanged.
- Pattern adapted from `openclaw chat` / `openclaw tui --local`.

### `huntova security audit` (new)
- 10 local checks: file modes (config.toml, secrets.enc, db.sqlite,
  daemon plists), plaintext-fallback detection, env-leak grep on
  config.toml, dev-fallback HV_SECRET_KEY, proxy MITM warnings,
  keyring backend in use.
- Per-check severity 1/2/3 + remediation hint.
- `--json` flag for scripted runs.
- Exit code: 1 on any sev-1, 2 on sev-2 only, 0 otherwise.

### Onboard — OpenClaw-parity flag set
- `--gemini-api-key` / `--anthropic-api-key` / `--openai-api-key` /
  `--openrouter-api-key` / `--groq-api-key` / `--deepseek-api-key` /
  `--together-api-key` / `--mistral-api-key` / `--perplexity-api-key`
  / `--ollama-api-key` / `--lmstudio-api-key` / `--llamafile-api-key`
  / `--custom-base-url` / `--custom-api-key` / `--custom-model`.
- `--reset-scope {config,keys,full}` — wipe state before re-running.
- `--flow {quickstart,advanced,manual}`, `--mode {local,remote}`.
- `--accept-risk` — required pairing with `--no-prompt`. Mirrors
  OpenClaw's non-interactive safety policy.
- `--json` — structured summary on completion for CI scripts.
- Reset-scope wipe semantics: config = TOML only · keys = TOML +
  every HV_*_KEY in keychain · full = TOML + keychain + local DB.

### `huntova config` — new subcommands
- `unset <key>` — delete a key from config.toml. Refuses on
  multi-line array values to avoid silent corruption (use
  `huntova config edit` for those). Refuses on secret-looking keys
  (those live in the keychain).
- `validate` — TOML parse + schema check (preferred_provider,
  hunting.default_countries, hunting.max_leads_per_hunt, etc.).
- Pattern adapted from `openclaw config unset` / `openclaw config
  validate`.

### Hunt budget controls
- Two new inputs in the Start Hunt popup: "Max leads this hunt" and
  "Stop after N minutes". Empty = unlimited (current default).
- `agent_runner.start_agent` validates `[1, 500]` / `[1, 120]`
  server-side and silently falls back to None on malformed input.
- `app.py:_check_budget()` probes at top of each query loop AND at
  post-batch breakpoint. Emits log + status SSE on cap-fire.

### SaaS-y profile UI stripped in local mode
- The user-menu top-right defaults to a settings-gear SVG instead of
  a "U" initial avatar.
- `.hv-saas-only` class hides email / plan tier / log-out / "user@
  email.com" when `single_user_mode=true`.
- `hvLoadAccount` no longer overwrites the gear with a "U" letter
  in local mode (see Bug Fix #2 below).

### Install.sh polish
- Windows / MINGW / MSYS / CYGWIN guard with PowerShell-specific
  hint (winget install Python → pipx → huntova).
- Post-install PATH verification (`command -v huntova`).
- `pipx inject huntova questionary` so first-run users see the
  polished TUI, not the fallback.
- `pipx list --short` for stable detection (was: drift-prone grep).

### Per-provider model selection
- `config.py:TIER_MODELS` is now provider-aware. Anthropic users get
  `claude-sonnet` / `claude-opus`, OpenAI users get `gpt-4o-mini` /
  `gpt-5`, Ollama users get `llama3.2`, etc.
- Reads `HV_<PROVIDER>_MODEL_PRO` env overrides.

### Documentation
- `CONTRIBUTING.md` — bug-report flow, dev-checkout setup, PR checklist.
- `.github/ISSUE_TEMPLATE/bug_report.md` + `feature_request.md`.
- `.github/PULL_REQUEST_TEMPLATE.md`.
- `docs/PLUGINS.md` — full plugin API surface (8 hooks, capability
  classes, minimal example, registry submission).
- `docs/CHAT.md` — chat command prompt shape + dispatch flow.
- `NOTICE.md` — every OpenClaw-port adaptation catalogued with the
  source file/concept it was lifted from. MIT license text reproduced
  in full.

## Bug fixes

- **CRIT — `huntova onboard --browser` crash.** `class _Args: port =
  port` body resolved RHS in (empty) class namespace. Replaced with
  `SimpleNamespace`. Without this the documented browser-mode path
  was completely broken.
- **CRIT — Per-provider model selection crash.** `config.py:TIER_MODELS`
  was hardcoding Gemini IDs. Anthropic / OpenAI / Ollama users would
  pass a Gemini model string to a non-Gemini SDK and crash.
- **CRIT — `huntova chat` broken for Anthropic users.** Anthropic SDK
  has no `response_format=json_object`; Claude returned prose,
  every chat turn failed. Fix: prefill `{` and concat on receive.
- **CRIT — Local-mode gear icon clobbered.** `hvLoadAccount()` overwrote
  the settings-gear SVG with "U" letter. Fix: gate avatar mutation on
  `!single_user_mode`.
- **HIGH — `huntova config unset` corrupted multi-line arrays.** Line-
  based delete dropped only the opening `key = [` and left orphans.
  Now refuses with a clear message pointing to `huntova config edit`.
- **HIGH — `huntova doctor` exit 0 on broken install.** AI-probe-skipped
  path returned 0 unconditionally, ignoring the `fail` flag set by
  earlier checks. Now `1 if fail else 0`.
- **HIGH — Keychain read failures swallowed.** `_hydrate_env_from_local
  _config` had `except Exception: pass`. Locked keychain looked
  identical to "no key configured" and the user was sent to re-onboard
  a key that was already there. Now surfaces a stderr line.
- **HIGH — install.sh post-install fail.** `exec huntova onboard` would
  fail with `command not found` if pipx hadn't sourced ensurepath yet.
  Now verifies `command -v huntova` and prepends `~/.local/bin`.
- **HIGH — install.sh missing Windows guard.** Curl-pipe-sh from
  PowerShell silently dumped garbage. Now exits early with a winget+
  pipx hint.
- **HIGH — admin endpoints timing-attack vulnerable.** `==` token
  compare on `/api/admin/cloud-token` + `/api/admin/metrics`. Switched
  to `hmac.compare_digest`.
- **HIGH — `/api/setup/key` fail-open on runtime import error.**
  Circular-import or dev-mode reload would silently bypass the cloud-
  mode gate and accept keychain writes from a network listener. Now
  fails closed with 503.
- **HIGH — systemd `Environment=` not escaped.** Backslash, double-
  quote, and dollar in keychain blobs produced malformed unit files
  that silently never started. Now escaped.
- **HIGH — `cmd_status` global socket timeout leak.** `socket.setdefault
  timeout(0.5)` corrupted every later network call in the same
  process. Removed; `urlopen` already gets `timeout=0.5` explicitly.
- **HIGH — OG-SVG slug bypass.** `/h/<slug>/og.svg` accepted any path
  segment and queried the DB. Now validates against `[A-Za-z0-9_-]
  {4,32}` like the other `/h/<slug>` routes.
- **MED — `huntova plugins ls` rejected as `invalid choice`.** Aliased
  to `list` at the parser layer.
- **MED — `huntova config show` exit 1 on missing config.** Fresh
  install showed exit code 1 from a `show` command. Now exits 0 with
  a friendly "not yet created" message pointing to onboard.
- **MED — `huntova share <slug>` rejected by argparse.** Dropped the
  `choices=()` restriction; `cmd_share` recognises slug-shaped tokens.
- **MED — `huntova test-integrations` exit 1 on vanilla install.**
  Counted "not configured" as failures. Now skip-vs-fail is explicit.
- **MED — Stale "huntova init" recommendations.** Replaced with
  "huntova onboard" in doctor / status / providers messaging.

## Known bugs (still to fix)

- **MED — `--reset-scope keys` doesn't unset env vars hydrated for the
  same process.** If you run `huntova onboard --reset-scope keys` from
  a shell that exports `HV_GEMINI_KEY`, the wizard sees the env var
  still set and offers to skip provider setup. Workaround: open a
  fresh shell. Fix planned for v0.1.0a4 (3 lines).
- **MED — Hunt timeout doesn't fire mid-query.** `_check_budget()`
  runs at iteration boundaries only. A single query (Playwright deep-
  qualify + contact enrichment + AI scoring) can take 10+ minutes;
  user sets a 5-minute cap, agent hits 5min mid-query, completes that
  query, then stops. Fix: add per-URL probe in `qualify`/`process_
  result`. Planned for v0.1.0a4.
- **MED — Chat `start_hunt` ignores AI-supplied `timeout_minutes`.**
  System prompt asks for it, AI returns it, but `_dispatch_hunt` and
  `cmd_hunt → start_agent` don't forward it to the new
  `agent_runner` budget cap. Fix planned for v0.1.0a4.
- **LOW — Smart-learning loop is partial.** `user_learning_profile.
  instruction_summary` flows through end-to-end, but the
  `recipe-adapter` / `adaptation-rules` plugins fire only on the CLI
  `huntova recipe run` path — web hunts don't set
  `HV_RECIPE_ADAPTATION` env. README is honest about what learns;
  the plumbing fix is post-launch.
- **LOW — Top-level `--help` dump is overwhelming.** 27 subcommands
  rendered on a wrapped line. Cosmetic. Plan: group help under
  "Getting started: onboard, doctor, status, hunt".

## Repo / release process

- This is the canonical RELEASE file format going forward. Every
  release ships a `RELEASE-v<version>.md` at the repo root with this
  structure: Updates / Bug fixes / Known bugs.
- Pushes go to `https://github.com/enzostrano/huntova-public` only.
  The legacy private repo is no longer maintained.

## Credits

**Brain:** Enzo (@enzostrano).

**Coding:** Claude (Anthropic), via Claude Code.

Huntova would not exist without Anthropic's models. Thank you to the
Anthropic team for building the tool that built Huntova. That's why
Claude is the default provider — it's the model that shipped this thing.
