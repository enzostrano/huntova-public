# Huntova v0.1.0a10 — 2026-04-30 (eighth drop today)

The "round-9 audit + huntova install-completion" release. Round-9
caught a regression in v0.1.0a9's daemon-log fix, plus 5 quality
improvements in benchmark / TOML / args.force prophylaxis. Plus
the missing one-command `huntova install-completion` for shell
completion auto-install.

## Updates

### `huntova install-completion` — one-command shell completion install
- Auto-detects shell from `$SHELL`, or `--shell zsh|bash|fish`.
- Writes static files only (no shell-eval). Idempotent rc-patching.
- zsh: `~/.zsh-completions/_huntova` + `fpath` snippet in `~/.zshrc`
- bash: `~/.bash_completion.d/huntova` + source-line in `~/.bashrc`
- fish: `~/.config/fish/completions/huntova.fish` (auto-loaded)
- `--uninstall` reverses everything cleanly.
- `--dry-run` shows what would be written.
- The legacy `huntova completion <shell>` (prints to stdout) still
  works unchanged for users with custom setups.
- Pattern from `openclaw completion install`.

## Bug fixes (round-9 audit on v0.1.0a9)

- **HIGH — `huntova logs` daemon dedupe regressed.** v0.1.0a8's
  fix used a tail-relative line number as the dedupe key. As the
  file grew, the same physical line got a fresh `ts` each poll and
  re-printed forever. Now uses byte-offset from start-of-file as
  the key (truly stable), tracks `_DAEMON_LAST_POS` per file across
  follow-mode polls so only newly-appended bytes are emitted.
- **HIGH — Grouped `--help` silently swallowed formatter errors.**
  `except Exception: pass` hid bugs (e.g. typo in `_HELP_CATEGORIES`
  that lost a subcommand from grouping). Now logs to stderr before
  falling back to the default formatter.
- **MED — `_approx_tokens` was provider-blind.** `len(text)//4`
  under-counted Anthropic by ~21% and Gemini by ~14%, distorting
  benchmark cost-est. Now uses `_CHARS_PER_TOKEN = {"anthropic":
  3.3, "gemini": 3.5, "openai": 4.0}` divisor map.
- **MED — Benchmark score-parse accepted JSON-but-no-scores garbage.**
  Stray `{...}` inside chatty Anthropic responses parsed as a dict
  with no expected keys, silently producing all-zeros and skewing
  score-stability. Now requires ≥3 of 5 expected keys before
  accepting; otherwise treated as parse failure.
- **MED — `_toml_key` didn't escape control chars.** `\b`, `\f`,
  `\n`, `\r`, NUL emitted raw inside the quoted string crashed
  `tomllib` on re-import. Now full TOML basic-string escape set.
- **LOW — Other `args.force` sites still vulnerable to the round-7
  AttributeError pattern.** Applied `getattr(args, "force", False)`
  prophylactically at 3 more sites (`plugins install`, `plugins
  create`, `recipe save` overwrite check) so a future parser
  refactor can't silently re-introduce the bug.

## Carry-over (still in ROADMAP)

- `recipe-adapter` plugins fire only on CLI `recipe run`.
- Light-theme palette not yet complete.
- Keychain sentinel auto-clear (commented-but-not-implemented).

## Repo / release process

- Pushes go to `enzostrano/huntova-public` ONLY.
- All 72 tests pass on this release.

## Credits

**Brain:** Enzo (@enzostrano).

**Coding:** Claude (Anthropic), via Claude Code.

Thank you Anthropic.
