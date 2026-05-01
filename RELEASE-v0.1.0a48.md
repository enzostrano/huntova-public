# Huntova v0.1.0a48 — 2026-05-01

Three more agent-found bugs landed. One real security hardening,
one SSE memory-leak fix, one a11y nit.

## Bug fixes

### `secrets_store.py` plaintext + Fernet writes are now atomic 0600
- Was: `Path.write_text/write_bytes` creates the file with the
  process umask (typically 0644 — world-readable on Unix), THEN
  `_harden_perms` chmod's to 0600. A racing reader can slurp the
  plaintext secrets in the window between create and chmod.
- New `_atomic_write_0600()` opens a temp sibling with
  `O_CREAT|O_EXCL|O_WRONLY|0600`, writes + fsync's, then `os.replace`s
  over the target. No window where the file is world-readable.
- Both `_plain_write` and `_fernet_write` now route through it.
- Windows path falls back to plain `write_bytes` (chmod 0600 is a
  no-op there anyway).

### `user_context.UserEventBus.emit` dead-subscriber sweep
- Was: a queue that hit `_DEAD_THRESHOLD` got marked dead only if
  emit() *that call* hit it. If it drained one slot between emits,
  it'd stay in `_subscribers` forever — a slow leak on long-running
  cloud deploys with disconnected SSE clients.
- Now: after the per-emit dead set is processed, run an unconditional
  sweep of `_subscribers` and discard any whose `qsize()` is at or
  above the threshold. O(N) per emit but N is tiny (one per active
  user).
- Also fixed: iterating `self._subscribers` directly was unsafe if
  another thread mutated it via subscribe/unsubscribe during emit.
  Now iterates `list(self._subscribers)`.

### Dashboard `um-plan-card` div needed accessibility
- Was: `<div class="um-plan-card" onclick="...">` — a clickable
  div without `role="button"`, no keyboard handler, no aria-label.
  Screen readers couldn't reach it.
- Now: added `role="button"`, `tabindex="0"`, `aria-label="Upgrade
  plan"`, and an `onkeydown` handler so Enter / Space activate the
  same way a click does.

## Updates
- None.

## Known issues
- Same as a47.
