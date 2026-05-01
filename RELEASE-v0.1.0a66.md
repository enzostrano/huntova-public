# Huntova v0.1.0a66 — 2026-05-01

Two more agent-found bugs.

## Bug fixes

### `tui.password()` strips whitespace from API keys
- Was: `getpass.getpass()` returned the raw input. Users pasting
  a key with accidental leading/trailing whitespace (`" sk-abc..."` —
  many shells / paste buffers add that) saved the bogus string to
  the keychain. Every subsequent auth call then failed with no
  obvious cause (the displayed key looks correct).
- Now: `.strip()` applied on both the questionary path and the
  getpass fallback. Keychain only ever holds clean values.

### `is_user_blocked` rejects bare-TLD entries in blocklist
- A user adding "com" to their blocklist (typo or paste accident)
  used to block every .com domain in the app — `d.endswith("." + b)`
  would match `gmail.com`, `microsoft.com`, etc.
- Now: requires at least one dot in the blocked entry before the
  endswith match runs. Bare TLDs are silently dropped from the
  match set.

## Updates
- None.

## Known issues
- Same as a65.
