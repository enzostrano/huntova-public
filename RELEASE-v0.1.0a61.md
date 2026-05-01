# Huntova v0.1.0a61 — 2026-05-01

Two more agent-found bugs.

## Bug fixes

### `saveSession` quota error no longer silently swallowed
- Was: `try{ localStorage.setItem(...) }catch(e){}` — bare swallow.
  When the browser's localStorage quota was exceeded (Safari
  private mode, large session log accumulation, multi-tab
  conflict), the save silently failed. User couldn't resume
  session after a refresh and had no clue why.
- Now: detects `QuotaExceededError` (and quota-keyword fallback),
  purges the stale key to free roughly the bytes we tried to write,
  and surfaces a one-time toast: "Browser storage full — session
  resume disabled". Other errors get logged via `console.warn` so
  devs can debug without spamming the user.

### `/api/bulk-update` validates status + caps batch
- Was: accepted any string for `email_status`. A bot, typo, or
  hostile client could write `"pwned"`, multi-MB strings, or
  random Unicode into the `email_status` column, polluting filter
  dropdowns + status_history. Plus no cap on `lead_ids[]` length —
  a 100k-id submission would force a full scan.
- Now: whitelists `{"new", "email_sent", "followed_up", "replied",
  "meeting_booked", "won", "lost", "ignored"}` (matches the
  dashboard's bulk-status `<select>`). 400 on invalid input.
- Plus: caps `lead_ids` set to 500 per request. 400 on overflow.

## Updates
- None.

## Known issues
- Same as a60.
