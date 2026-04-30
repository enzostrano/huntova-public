# Huntova v0.1.0a9 — 2026-04-30 (seventh drop today)

The "round-8 audit + huntova benchmark + grouped help" release.
Closes 5 audit findings (2 launch-blocker SEV-1s caught the day they
shipped). Adds synthetic-hunt provider benchmarking. Top-level help
is now grouped by category instead of an alphabetical 30-command dump.

## Updates

### `huntova benchmark` — synthetic-hunt provider quality measurement
- `huntova benchmark run [--provider P]` — run a 3-fixture synthetic
  hunt. Records score-mean, score-stability across 3 runs, latency
  p50/p90, estimated cost per provider. Iterates every configured
  provider unless `--provider` is given.
- `huntova benchmark compare` — table view of past runs.
- `huntova benchmark fixtures` — list / preview the canned pages.
- Persists runs in `~/.local/share/huntova/benchmarks.json`.
- Pattern adapted from openclaw bench. New `cli_benchmark.py`.

### `huntova --help` is now grouped by category
- 30+ subcommands no longer rendered as one alphabetical wrapped
  line. Categories: Getting started · Daily use · Outreach ·
  Plugins / customization · Daemon / ops · Utility.
- Per-subcommand `--help` unaffected. Defensive try/except around
  the format_help override so a formatting bug can never break
  `huntova --help`.

## Bug fixes (round-8 audit on v0.1.0a8)

- **CRIT — `cmd_recipe_import` AttributeError on every first-time
  import.** `args.force` was read inside the new `_run_import()`
  coroutine without `getattr` defaulting. Recipients running
  `huntova recipe import shared.toml` for the first time hit a hard
  crash before any DB work — the round-7 fix was dead on arrival.
  Fix: `getattr(args, "force", False)`.
- **CRIT — `_toml_dump_section` emitted invalid TOML for keys
  containing `.`.** A wizard key like `"domain.tld"` got written as
  `domain.tld = "..."` which TOML parses as nested table key
  `[domain].tld`, not a string key. Re-import then either errors or
  silently produces wrong shape. Fix: new `_toml_key()` helper
  quotes any key containing chars outside `[A-Za-z0-9_-]`. Applied
  to bare-key emit + recursive sub-table headers.
- **HIGH — `huntova logs --follow` dedupe set unbounded.** Long
  follow sessions accumulated one tuple per emitted event with no
  eviction. Fix: bounded to 5000 most-recent via `collections.deque`
  + set, evicts FIFO.
- **MED — Daemon follow mode collapsed identical recurring lines.**
  Daemon entries had `ts=""`, so two distinct `"connection refused"`
  lines hashed identically and only one printed. Fix: `_load_daemon`
  emits `ts=f"{file}:{lineno}"` so each physical line stays unique.
- **MED — Keychain sentinel comment promised auto-clear that wasn't
  implemented.** (Documented as known limitation; auto-clear in
  secrets_store.get_secret is a v0.2 task.)

## Carry-over (still in ROADMAP)

- `recipe-adapter` plugins fire only on CLI `recipe run` (post-launch).
- Native macOS app shell (v0.3+).
- Plugin sandbox (v0.3+).

## Repo / release process

- Pushes go to `enzostrano/huntova-public` ONLY.
- All 72 tests pass on this release.

## Credits

**Brain:** Enzo (@enzostrano).

**Coding:** Claude (Anthropic), via Claude Code.

Thank you Anthropic.
