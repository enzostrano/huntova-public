# Huntova v0.1.0a65 — 2026-05-01

Two more agent-found bugs.

## Bug fixes

### Query post-filter banned-patterns now run BEFORE strip
- `_BANNED_PATTERNS` includes `r'"[^"]{3,}"'` to reject quoted-phrase
  queries (SearXNG doesn't honour them). But the destructive
  `q.replace('"', '')` ran on the line BEFORE the banned-patterns
  loop — quotes were gone, so the regex never matched.
- Reordered: check banned patterns first (operators + quoted
  phrases + boolean operators), then strip / normalise. Quoted
  phrases now actually get rejected as intended.

### `cmd_security` accurate plaintext-fallback diagnostic
- Was: a stale `~/.config/huntova/secrets.json` always reported
  "keychain fallback engaged" even when the *active* backend was
  keyring or encrypted-file. Misled users who'd successfully
  migrated to a modern backend but didn't realise the legacy file
  was still on disk.
- Now: probes `secrets_store._backend_label()` and tailors the
  warning. If keyring/encrypted-file is active → "legacy file,
  safe to remove". If plaintext is the actual active backend →
  the original "install keyring or cryptography" advice.

## Updates
- None.

## Known issues
- Same as a64.
