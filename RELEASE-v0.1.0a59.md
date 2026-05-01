# Huntova v0.1.0a59 — 2026-05-01

## Bug fixes

### `iwSendAssist` double-submit guard
- Wizard chat assist had no in-flight guard. Pressing Enter and
  clicking Send within the same tick (or rapid Enter spam) fired
  the same prompt to `/api/wizard/assist` two or more times,
  doubling the assistant's reply in the chat log + spending the
  user's API budget twice.
- Now: `_iwSendingAssist` flag set on entry, cleared in `.finally()`
  after the fetch resolves (success, error, or network failure).
  Subsequent calls bail until the in-flight one completes.

## Updates
- None.

## Known issues
- Same as a58.
