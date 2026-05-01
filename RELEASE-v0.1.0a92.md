# Huntova v0.1.0a92 — 2026-05-01

## Bug fixes

### `/api/update` 404 response shape matches the rest of the API
- The 404 branch returned `{"error": "not found"}` while every other
  failure path returns `{"ok": False, "error": "..."}`. The frontend
  `if(!d.ok) toast(d.error)` short-circuited on the missing `ok`
  field, so users who hit a stale lead-id (e.g. an open tab after
  the lead was deleted in another tab) saw the generic "Update
  failed: server error" instead of "not found."
- Now returns the canonical `{"ok": False, "error": "not found"}`.

## Updates
- None.

## Known issues
- Same as a91.
