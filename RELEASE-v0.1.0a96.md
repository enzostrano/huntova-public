# Huntova v0.1.0a96 — 2026-05-01

## Bug fixes

### `status_history` skips consecutive duplicates and is capped at 100
- The lead-update mutator appended a `{status, date}` row to the
  status-history list every time `/api/update` was called with an
  `email_status` body, even when the new status equalled the old.
  A double-click on the same status pill, or a retry by the frontend
  after a timeout, wrote two identical rows. Over months, the list
  grew unbounded — the lead-detail timeline became wallpaper.
- Now skips the append when the most recent history entry already
  has that status, and trims the list to the last 100 entries when
  it grows past that cap. The first append still fires; the
  `email_status_date` field still updates either way (so the "Last
  touch" pill keeps reflecting the latest interaction).

## Updates
- None.

## Known issues
- Same as a95.
