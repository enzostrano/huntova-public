# Huntova v0.1.0a4 — 2026-04-30 (later same day)

The "actually-smart loop" release. Plus polished install + onboard +
the settings/memory/personality polish from continuing the OpenClaw
study.

## Updates (what shipped)

### Real adaptive learning — feedback now reaches the DNA prompt
- **`app.py:_dna_build_stage_1_prompt`** — `_feedback_good` /
  `_feedback_bad` lists are now read by the strategy-generation prompt.
  Lead summaries (`org_name + country + why_fit + production_gap`) get
  injected as POSITIVE PATTERNS and AVOID PATTERNS sections with
  explicit directives: positives steer `hunting_channels` /
  `scoring_guide.bonus_signals` / `must_have_signals`; negatives steer
  `anti_patterns.*` and `instant_reject`.
- **`app.py:_dna_build_stage_2_prompt`** — the queries/rules
  generation prompt also reads the feedback and adds two new
  KILL CHECKLIST items: "Would likely surface a page resembling the
  AVOID PATTERNS list" and "Misses the shape of the POSITIVE PATTERNS".
- **DNA payload** now exposes `_adapted_from_feedback` /
  `_feedback_good_count` / `_feedback_bad_count` so we can verify
  adaptation actually fired (vs. the old behaviour where only the
  version field bumped).
- Backwards compatible — no feedback = unchanged behaviour.

Earlier audit had flagged the smart-loop as cosmetic (the version was
bumping but the regenerated DNA was identical). This release closes
the loop: clicking Good Fit / Bad Fit on leads now genuinely shifts
the next hunt's queries + scoring.

### OpenClaw-style banner: randomized tagline + config-detected card
- `tui.py:print_banner` now rotates one of 10 witty taglines under
  the ASCII logo. Pattern from OpenClaw's banner ("If something's on
  fire, I can't extinguish it…"). Examples:
  - "evidence-first prospecting — every fit score has a receipt."
  - "default model: Claude. yes, the irony of an AI built using AI is
    not lost on me."
  - "13 AI providers, 1 SearXNG, 0 vendor lock-in."
- `tui.py:config_summary_card` — boxed key-value card mirroring
  OpenClaw's "Existing config detected" panel. Shows `config /
  preferred_provider / providers configured / secrets backend` to
  returning users before any prompts.
- `cmd_onboard` calls the card when `~/.config/huntova/config.toml`
  already exists. First-run users see no card (nothing to summarise).

### Single-command install with chat-style banter
- `static/install.sh` rewritten as a personality-led flow:
  - HUNTOVA ASCII logo banner up top in purple
  - 4 phases announced as `🦊 huntova:` chat lines (instead of silent
    "▸ Installing pipx" steps)
  - Funny aside lines ("downloading chromium. this is the slowest
    part — try the kettle.")
  - Boxed success card with next-step hint
  - Falls into `exec huntova onboard` automatically
- One-liner install: `curl -fsSL huntova.com/install.sh | sh`
  (until DNS/CDN is wired, the GitHub raw URL works:
  `curl -fsSL https://raw.githubusercontent.com/enzostrano/huntova-public/main/static/install.sh | sh`)
- Installs from the public-repo git URL via pipx — works today
  without huntova being on PyPI yet.

### Landing-page hero install command
- `templates/landing.html` — single-command install code block at
  the top of the hero, with copy button. Matches OpenClaw's website
  pattern.
- Hero-note text reframed to credit Claude as the build model.

### Default provider switched to Anthropic Claude
- `HV_AI_PROVIDER` default is `anthropic`. Huntova was built using
  Claude end-to-end and ships with the model that gave the best
  agent quality during development.
- `_DEFAULT_ORDER` in `providers.py` puts `anthropic` first.
- Pre-selected as default in TUI wizard + hero text.

### Credits rewritten
- README "Credits" section: **Brain** = Enzo (@enzostrano).
  **Coding** = Claude (Anthropic) via Claude Code.
- "Huntova would not exist without Anthropic's models. Thank you to
  the Anthropic team for building the tool that built Huntova."

### `setup.py` shim
- Single-line shim so pip < 21.3 can install Huntova without
  pyproject.toml-only errors. Modern pip ignores it.

## Bug fixes

- **CRIT — `huntova chat` broken for Anthropic users.** Anthropic
  has no `response_format=json_object`. Fix: prefill assistant turn
  with `{` and concat on receive. The new default provider's
  flagship feature now works.
- **CRIT — Local-mode gear icon clobbered by `hvLoadAccount`.**
  `avatar.textContent='U'` overwrote the SVG. Fix: gate avatar
  mutation on `!single_user_mode`.
- **HIGH — `huntova config unset` corrupted multi-line TOML arrays.**
  Now refuses with a pointer to `huntova config edit`.
- **HIGH — pip 21.2 / Python 3.9 install friction.** Auto-upgrade pip
  in install.sh when < 21.3. README clone-install block now uses
  `python3.13` explicitly with explainer.
- **HIGH — install.sh missing post-install PATH check.** Fixed in
  v0.1.0a3, retained.
- **HIGH — Per-provider model selection crash for Anthropic / OpenAI
  / Ollama users.** Fixed in v0.1.0a3, retained.
- **MED — `huntova doctor` exit 0 on broken install.** Fixed in
  v0.1.0a3, retained.
- **MED — Keychain read failures swallowed silently.** Fixed in
  v0.1.0a3, retained.

## Known bugs (still to fix)

- **MED — `--reset-scope keys` doesn't unset env vars hydrated for
  the same process.** Workaround: open a fresh shell. Fix planned
  for v0.1.0a5 (3 lines).
- **MED — Hunt timeout doesn't fire mid-query.** `_check_budget()`
  runs at iteration boundaries only. A single Playwright deep-qualify
  + scoring can take 10+ minutes. Fix: per-URL probe in `qualify`.
  Planned for v0.1.0a5.
- **MED — Chat `start_hunt` ignores AI-supplied `timeout_minutes`.**
  System prompt asks for it, AI returns it, but `_dispatch_hunt`
  doesn't forward it to `agent_runner`'s budget cap. Planned for
  v0.1.0a5.
- **LOW — `recipe-adapter` / `adaptation-rules` plugins fire only
  on the CLI `huntova recipe run` path.** Web hunts don't set
  `HV_RECIPE_ADAPTATION` env so those plugins are no-ops on the
  hosted/dashboard path. The new DNA-prompt feedback wiring (above)
  is the primary smart-loop now; the adapter plugins are secondary
  and can be migrated in a future cleanup.
- **LOW — Top-level `huntova --help` dump is overwhelming.**
  27 subcommands wrapped on one line. Cosmetic.
- **LOW — Settings UI gaps.** Per-plugin enable/disable toggles, CRM/
  webhook config UI, theme/reduced-motion/telemetry toggles, account
  JSON export — all identified by the audit, agent currently building.
  Will land in v0.1.0a5 or v0.1.0a6.

## Repo / release process

- Per the new push policy: pushes go to `enzostrano/huntova-public`
  ONLY. The legacy private repo is no longer maintained.
- Each release ships a `RELEASE-v<version>.md` at the repo root.
  `CHANGELOG.md` is the human-facing summary; `RELEASE-v*.md` is
  the per-version durable record.

## Credits

**Brain:** Enzo (@enzostrano).

**Coding:** Claude (Anthropic), via Claude Code.

Huntova would not exist without Anthropic's models. Thank you to the
Anthropic team for building the tool that built Huntova. That's why
Claude is the default provider — it's the model that shipped this thing.
