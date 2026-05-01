# Huntova v0.1.0a8 — 2026-04-30 (sixth drop today)

The "round-7 audit caught everything" release. Six audit findings
fixed (mostly in the just-shipped recipe export/import code), plus
new `huntova logs` unified log viewer, plus the onboard cheat-sheet
polish + 2 carry-over known-bug fixes.

## Updates

### `huntova logs` — unified log viewer
- `huntova logs tail [--follow] [--since 1h]` — merges agent_runs +
  agent_run_logs + lead_actions, DESC by ts. `--since` accepts
  "1h" / "30m" / "2d" / "90s".
- `huntova logs hunt <run_id>` — every event for one hunt.
- `huntova logs daemon [--follow]` — tails ~/.local/share/huntova/
  logs/daemon.{out,err}.
- `huntova logs filter --level {error,warn,info,debug}` — severity
  floor across all sources.
- `--json` flag on every subcommand. `--follow` polls every 2s,
  exits cleanly on Ctrl+C.
- Pattern adapted from openclaw logs / openclaw tail.

### Onboard cheat sheet at end of wizard
- Both `_onboard_v2` (rich TUI) and `_onboard_v1` (legacy fallback)
  ending replaced with an 8-command cheat sheet:
  `serve / hunt --max-leads 5 / chat / examples ls / migrate
  from-csv / plugins ls / security audit / doctor`.
- New users now discover the surface they have, not just `serve`.

### `is_private_url` → `classify_url`
- New `app.classify_url()` returns `"ok" / "private" /
  "unresolvable" / "malformed"` for richer SSRF gate decisions.
- `is_private_url()` is a backwards-compat wrapper (returns True for
  anything except "ok").
- Callers can now distinguish "DNS failure" from "private IP" and
  give better error messages.

### Keychain warning de-duplicated
- Sentinel file `~/.config/huntova/.keychain_warned` so the keychain-
  read-failed warning fires once per machine instead of every CLI
  invocation. Deleting the sentinel re-enables the warning.

## Bug fixes (round-7 audit on v0.1.0a7)

- **HIGH — `_toml_dump_section` silently dropped nested wizard dicts.**
  Wizard payloads have `normalized_hunt_profile` and `training_dossier`
  nested dicts that vanished from exported TOML. Recipient's hunt got
  garbage results. Fix: emit `[name.key]` sub-tables instead of
  silently dropping.
- **HIGH — `cmd_recipe_import` 4 separate asyncio loops + no rollback.**
  If save_hunt_recipe failed AFTER merge_settings succeeded, the
  wizard was already mutated with no recipe row. Fix: single
  `_run_import()` async coroutine, partial failures explicitly
  flagged with remediation hint.
- **HIGH — `_is_secret_key` missed bare `apikey`-style keys.** Added
  `apikey` / `credential` / `auth` / `pwd` to substring hints, plus
  `endswith("key")` (not just `_key`) so camelCase doesn't slip
  through.
- **MED — `_toml_value(None)` encoded as empty string.** Re-import
  turned previously-unset fields into deliberate `""`, breaking
  export-then-import idempotency. Now returns None and callers skip.
- **MED — Empty `[scoring_rules]` could not be represented on
  import.** Now distinguishes "key absent" from "key present but
  empty list" so a "clean slate" recipe imports correctly.
- **LOW — Post-batch `_check_budget()` re-fired after in-batch
  trigger.** Caused duplicate "stopped" SSE events. Fix: skip the
  post-batch probe when `_stop_reason` is already set.

## Known bugs (still to fix — moved to ROADMAP)

- `recipe-adapter` plugins fire only on CLI `huntova recipe run`.
- Top-level `huntova --help` 27-subcommand dump still overwhelming.

## Repo / release process

- Pushes go to `enzostrano/huntova-public` ONLY.
- Each release ships `RELEASE-v<version>.md`.
- All 72 tests pass on this release.

## Credits

**Brain:** Enzo (@enzostrano).

**Coding:** Claude (Anthropic), via Claude Code.

Thank you Anthropic.
