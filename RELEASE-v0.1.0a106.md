# Huntova v0.1.0a106 — 2026-05-01

## Bug fixes

### `/api/export/json` adds `default=str` to `json.dumps`
- The leads JSON export called `json.dumps(leads, ensure_ascii=False, indent=2)` without a fallback serializer. The
  `/api/export/account-data` (full backup) endpoint already passed
  `default=str`. If a lead row ever carried a value that wasn't
  natively JSON-serializable — datetime, Decimal, a Postgres
  `Decimal` from a numeric column, or a Python `set` slipped in by a
  future field — the export crashed with `TypeError` instead of
  stringifying gracefully.
- Now matches the account-export call shape and stringifies fallback
  types instead of failing the request.

## Updates
- None.

## Known issues
- Same as a105.
