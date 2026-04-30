# Third-Party Notices

Huntova is licensed under **AGPL-3.0-or-later** (see `LICENSE`).

This file lists external work whose **structural patterns** influenced
parts of this codebase, along with the licenses under which that work
was obtained. None of the original third-party code is reproduced
verbatim in Huntova; the modules listed below are independent
reimplementations in Python authored for Huntova specifically. They
are documented here for transparency and to acknowledge the prior art.

---

## OpenClaw — `@openclaw/openclaw`

- **Source:** https://github.com/openclaw/openclaw
- **License:** MIT
- **Copyright:** © OpenClaw contributors
- **Adaptation type:** structural / architectural inspiration only — no
  code or copyrighted text from OpenClaw appears in Huntova.

The following Huntova modules borrow architectural patterns observed in
OpenClaw's open-source reference implementation. Each is a fresh Python
implementation; no TypeScript was ported, and no UI copy, brand assets,
documentation prose, or product wording was reused.

| Huntova file                | Pattern adapted                                                                              |
|-----------------------------|-----------------------------------------------------------------------------------------------|
| `tui.py`                    | Wizard prompt-set shape (intro / outro / note / select / text / password / confirm / spinner). Backed by Python `questionary`; OpenClaw uses `@clack/prompts` (Node). |
| `huntova_daemon.py`         | Local-user daemon pattern — launchd LaunchAgent plist on macOS, systemd `--user` unit on Linux, with `huntova daemon install/uninstall/start/stop/status/logs` verbs. |
| `cli.py` — `cmd_onboard`    | Three-phase first-run wizard pattern: filesystem → provider/key → launch. Banner + step indicators. |
| `cli.py` — `_apply_reset_scope` | Three-tier reset semantics adapted from OpenClaw's `ResetScope = "config" \| "config+creds+sessions" \| "full"`. Huntova's tiers are renamed `config / keys / full` and operate on Huntova-specific filesystem state (config.toml, OS keychain entries for HV_*, local SQLite DB). |
| `cli.py` argparse epilogs   | "Docs:" footer pattern on subcommand help, mirroring OpenClaw's `addHelpText("after", ...)` on Commander.js. URLs point at Huntova's own docs in `huntova-public`. |
| `static/install.sh`         | Cross-platform shell installer pattern (Python detection → pipx bootstrap → optional Playwright dependency → final next-step hint). Includes Windows shell guard (MINGW/MSYS/CYGWIN) and post-install PATH verification. Independent shell implementation. |
| `templates/download.html`   | Animated terminal demo block on the marketing/install page. Independent CSS implementation. |
| `cli.py` — `cmd_chat`       | REPL-style natural-language CLI. Mirrors OpenClaw's `openclaw chat` (which is `tui --local`). Parses free text → JSON action via the user's configured provider, then dispatches in-process to existing `cmd_hunt` / `cmd_ls`. Independent Python implementation. |
| `cli.py` — onboard flag set | Per-provider flags (`--gemini-api-key` / `--anthropic-api-key` / etc. for all 13 providers + `--custom-base-url` / `--custom-api-key` / `--custom-model`), plus `--flow {quickstart, advanced, manual}` / `--mode {local, remote}` / `--accept-risk` / `--json`. Mirrors `openclaw onboard`'s flag shape, including the non-interactive safety policy that requires `--accept-risk` paired with `--no-prompt`. |
| `cli.py` — `cmd_config`     | `unset` and `validate` subcommands match `openclaw config unset` / `openclaw config validate`. Validation is Huntova-schema-specific (preferred_provider, hunting.default_countries, hunting.max_leads_per_hunt). |
| `cli_memory.py` — `huntova memory` | Lead-gen subset of `openclaw memory`: `search` (fuzzy text across saved leads), `inspect` (colored YAML dump of one lead), `recent` (last N days of hunts/leads/feedback/emails), `stats` (aggregate counts). Wired in `cli.py` via `cli_memory.register()`. Independent Python implementation operating on Huntova's PostgreSQL/SQLite tables (`leads`, `agent_runs`, `lead_feedback`, `lead_actions`); no OpenClaw source ported. |
| `cli_migrate.py` — `huntova migrate` | Lead-gen subset of `openclaw migrate`: `from-csv` (generic auto-detect), `from-apollo`, `from-clay`, `from-hunter` (predefined column maps for those exports), and `stats` (dry-run preview). Imports rows via the existing `db.upsert_lead`, dedupes by `org_website` and `contact_email`, supports `--dry-run`, `--force`, and repeatable `--map csv_col=lead_field` overrides. Wired in `cli.py` via `cli_migrate.register()`. Independent Python implementation; no OpenClaw source ported. |
| `cli_approve.py` — `huntova approve` | Lead-gen subset of `openclaw approve`: manual-approval queue for high-fit leads before outreach send. `queue` (list pending — `awaiting_approval` status OR `fit_score >= 8` unsent), `<lead_id>` (mark approved), `--top N` (bulk-approve top-N pending), `--reject <lead_id>` (counts as smart-loop feedback), `diff <lead_id>` (side-by-side AI draft email vs source-page evidence quote). Status mutation goes through the existing `db.merge_lead` row-locked RMW helper; audit rows go through `db.save_lead_action`. Approve only flips `status` — sending stays in `huntova outreach send`. Wired in `cli.py` via `cli_approve.register()`. Independent Python implementation; no OpenClaw source ported. |
| `cli_logs.py` — `huntova logs` | Lead-gen subset of `openclaw logs` / `openclaw tail`: unified log viewer for debugging a hunt. `tail [--follow] [--since 1h]` (cross-source feed across `agent_runs` / `agent_run_logs` / `lead_actions`, sorted DESC), `hunt <run_id>` (every event for one hunt: queries fired, leads found, scoring decisions, errors), `daemon` (tail `~/.local/share/huntova/logs/daemon.{out,err}` — mirrors `huntova daemon logs` in the unified surface), `filter --level {error,warn,info,debug}` (severity gate). All support `--json`. `--follow` polls every 2s and exits cleanly on Ctrl+C. Reuses `tui.py` color helpers and the `db_driver` connection pool. Wired in `cli.py` via `cli_logs.register()`. Independent Python implementation; no OpenClaw source ported. |
| `cli_benchmark.py` — `huntova benchmark` | Lead-gen subset of `openclaw bench`: synthetic-hunt provider quality benchmark that never burns real provider quota. `run [--provider P]` (run a synthetic 5-prospect hunt against 3 hardcoded HTML fixture archetypes — high-fit B2B agency, wrong-fit B2C consumer, boundary-case freelancer — record latency, JSON-validity, score-mean, score-stability across 3 repeats, and approximate USD cost; iterates every configured provider when `--provider` is omitted), `compare` (table of past runs from `~/.local/share/huntova/benchmarks.json`), `fixtures [--preview]` (list / preview the canned fixture pages). All support `--json`. Reuses `providers.get_provider({"preferred_provider": slug})` for dispatch and the `tui.py` color helpers; cost estimation uses a per-provider USD-per-1M-token table plus `tiktoken` if installed (else `len(prompt)/4`). Wired in `cli.py` via `cli_benchmark.register()`. Independent Python implementation; no OpenClaw source ported. |
| `templates/index.html`      | `.hv-saas-only` class hook so SaaS-only chrome (avatar / email / plan tier / log out) is hidden in local mode. Independent CSS / class taxonomy. |
| `cli.py` — `cmd_install_completion` | One-command shell-completion installer pattern, mirroring `openclaw completion install`. Auto-detects shell from `$SHELL`, writes static completion files (zsh: `~/.zsh-completions/_huntova`, bash: `~/.bash_completion.d/huntova`, fish: `~/.config/fish/completions/huntova.fish`), idempotently patches `~/.zshrc` / `~/.bashrc` (fish auto-loads), and supports `--uninstall` and `--dry-run`. Reuses `_BASH_COMPLETION` / `_ZSH_COMPLETION` / `_FISH_COMPLETION` strings via the `_completion_text()` helper shared with `cmd_completion`. No shell-eval — files only. Independent Python implementation. |
| `cli.py` — `cmd_recipe_export` / `_import` / `_diff` | Portable hunt-config TOML pack — share-with-a-colleague pattern. Mirrors OpenClaw's `recipe export` / `recipe import` shape (positional path, `--force` overwrite gate, secret-stripping on the way out, settings-merge on the way in). Independent Python implementation; minimal hand-rolled TOML writer (no `tomli_w` dep), reads back via stdlib `tomllib`. Strips `*_password`, `*_key`, `*_token`, `*_webhook*` keys recursively before write. Settings merge uses `db.merge_settings` (row-locked RMW). DNA regen via `app.generate_agent_dna` after import; failures are non-fatal. |

The MIT license requires that copyright and permission notices be
preserved when **substantial portions** of the source are copied. Since
no OpenClaw source is reproduced verbatim in Huntova, that condition
does not apply directly; the attribution table above is provided
voluntarily to credit the prior art.

The OpenClaw MIT license text is reproduced in full below.

```
MIT License

Copyright (c) OpenClaw contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

---

## Other dependencies

Standard third-party Python libraries used at runtime are listed in
`pyproject.toml`. Each carries its own license (most are MIT, BSD, or
Apache-2.0). Huntova does not redistribute their source; they are
declared as dependencies and installed by `pip` / `pipx`.

If you redistribute Huntova binaries that bundle these dependencies
(e.g. via PyInstaller or a Docker image), you are responsible for
preserving each dependency's own license file in the redistribution.

---

For questions about licensing or attribution, please open an issue on
the Huntova repository.
