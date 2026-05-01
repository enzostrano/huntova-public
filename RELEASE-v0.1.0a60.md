# Huntova v0.1.0a60 — 2026-05-01

60th release. Driver-agnostic cursor in the credit-delta path.

## Bug fixes

### `db.py:_apply_credit_delta_sync` cursor refactor
- Both cursors in this function (the UPDATE path + the WHERE-gate-
  failure fetch) were hardcoded to
  `cursor_factory=psycopg2.extras.RealDictCursor`. SQLite mode
  doesn't have that factory — would crash if the credit-delta path
  ever fires there.
- Now: `_cursor(conn)` driver-agnostic wrapper. Same pattern as
  a46 + a51 already established for admin_apply + merge_lead +
  merge_settings. Cloud Postgres behavior unchanged.

## Updates
- None.

## Known issues
- Same as a59.
