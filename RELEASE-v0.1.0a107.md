# Huntova v0.1.0a107 — 2026-05-01

## Bug fixes

### Account-export timestamps switched to UTC
- `/api/export/account-data` recorded `"exported_at"` and computed
  the download filename via `datetime.now()` — both naive and tied
  to the server's local timezone. A bundle exported on a US-East
  Railway worker carried a different time / day component than the
  same export from a US-West shard, which broke "as-of" comparisons
  in any GDPR audit trail downstream and confused users whose local
  clock didn't match the server.
- Both calls now use `datetime.now(timezone.utc)` so every bundle is
  pinned to UTC. Filename day rollover is also UTC-aligned, so two
  exports either side of midnight UTC don't collide on the same name.

## Updates
- None.

## Known issues
- Same as a106.
