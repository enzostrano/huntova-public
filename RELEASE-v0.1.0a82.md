# Huntova v0.1.0a82 — 2026-05-01

## Bug fixes

### Bulk-status dropdown was missing "New"
- The CRM bulk-action bar's status dropdown listed 7 of the 8
  valid statuses — `"new"` was missing. Users could change leads
  TO "Email Sent / Replied / Won / Lost / etc" in bulk, but
  couldn't bulk-reset back to "New" even though individual lead
  modals + the bulk-update API accept it (whitelist set in a61).
- Added `<option value="new">New</option>` after the placeholder.
  Parity with the 8-status whitelist restored.

## Updates
- None.

## Known issues
- Same as a81.
