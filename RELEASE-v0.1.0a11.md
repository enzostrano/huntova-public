# Huntova v0.1.0a11 — 2026-04-30 (ninth drop today)

The "carry-overs cleared" release. Round-10 audit caught a SEV-1 in
`huntova install-completion` (would shred user-installed `fpath`
lines on uninstall). Plus three carry-over items from the ROADMAP
finally landing: light-theme palette, keychain sentinel auto-clear,
and recipe-adapter plugin wiring for web hunts.

## Updates

### `huntova teach` — guided "show the agent good leads" flow
- Interactive: `huntova teach` pulls 5 random local leads, asks
  good-fit / bad-fit / skip per lead via arrow-key TUI. Records
  `lead_feedback` rows + triggers DNA refinement at the end.
- Bulk: `huntova teach --import <csv>` reads `org_name,verdict`
  rows, fuzzy-matches local leads, applies the verdicts.
- Status: `huntova teach status` shows current good/bad counts +
  progress to the next DNA refinement boundary.
- Same exact path the dashboard's good-fit/bad-fit buttons use
  (so feedback flows into the v0.1.0a4 adaptive smart-loop).

### Light-theme palette completed
- `static/style.css` `.light` block now overrides every dark-mode
  token (v6 + v7 parallel system). The Settings → Preferences theme
  toggle now produces a fully-themed light page instead of the
  half-light/half-dark mess.

### Keychain sentinel auto-clear (long-promised)
- `secrets_store.get_secret` now `unlink`s `~/.config/huntova/.keychain
  _warned` opportunistically on the first successful keychain read.
  Self-healing — if the user's keychain was locked then they unlocked
  it, the warning auto-clears next CLI run instead of staying
  silenced forever.

### Recipe-adapter plugins fire on web hunts (carry-over from v0.1.0a4)
- `agent_runner._run` now hydrates `HV_RECIPE_ADAPTATION` env from
  the user's saved `hunt_recipes.adaptation_json` if the start request
  named a `recipe_name`. The bundled `recipe-adapter` (pre_search) +
  `adaptation-rules` (post_score) plugins read this env and apply
  winning_terms / suppress_terms / scoring_rules. Cleared in finally
  so concurrent agents don't see each other's adaptation.
- The CLI `huntova recipe run <name>` path is unchanged.

## Bug fixes (round-10 audit on v0.1.0a10)

- **CRIT — `install-completion --uninstall` shredded user's own
  `fpath+=(~/.zsh-completions)` lines** added by prior tools (very
  common from `man zshcompsys`). Fix: dual-fence pattern with
  `_RC_FENCE_OPEN` + `_RC_FENCE_CLOSE` so we strip ONLY what we
  wrote. Backwards-compat path for v0.1.0a10 single-fence installs.
- **HIGH — Read-only rc files (`chmod 444`, immutable home,
  NixOS-managed config) crashed install-completion mid-install.**
  Now wraps reads/writes in try/except, prints a friendly hint
  ("add this manually: ..."), exits cleanly.
- **HIGH — `_DAEMON_LAST_POS` shared across one-shot → follow
  invocations in the same process.** A second `huntova logs daemon`
  call would skip everything emitted between the two reads. Fix:
  new `reset_daemon_state()` clears the dict at the top of each
  daemon-command dispatch.
- **HIGH — Daemon log follow couldn't detect file rotation
  (delete+recreate fresh inode).** Same cached pos was used against
  a different file. Fix: dict key is now `(name, st_ino)` so a fresh
  inode triggers a clean re-tail.
- **LOW — `_toml_key` had dead-code `or ch in "\\"` clause.**
  Backslash is already covered by the `>= 0x20` check. Removed.

## Repo / release process

- Pushes go to `enzostrano/huntova-public` ONLY.
- All 72 tests pass on this release.

## Credits

**Brain:** Enzo (@enzostrano).

**Coding:** Claude (Anthropic), via Claude Code.

Thank you Anthropic.
