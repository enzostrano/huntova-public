# Huntova v0.1.0a89 — 2026-05-01

## Bug fixes

### `BANNED_WORDS` covers the "I hope this finds you" family
- The drafted-email phrase blocklist documented `"I hope this finds you"`
  as a banned opener in two places (`_build_ai_context` + the system
  prompt), but `BANNED_WORDS` itself didn't include it. Drafts that
  opened with "I hope this email finds you well" / "I hope you're
  doing well" / "Just wanted to..." passed validation and shipped
  to the user as cold outreach.
- Added the seven most common offenders (case-insensitive match by
  the existing scanner): `i hope this finds you`, `i hope this email
  finds you`, `hope you're doing well`, `hope this finds you`, `hope
  this email finds you`, `just wanted to`, `i wanted to`.

### `stop_agent` releases a queued user's queue slot
- Clicking **Stop** while queued (i.e. another user's hunt is running
  and you're position 2/3/...) only set the per-user `action=stop`
  flag. The user_id stayed in `AgentRunner._queue`, so when the
  current hunt finished `_process_queue()` started theirs anyway,
  giving them a one-loop hunt they already cancelled — which then
  saw the stop flag and bailed, but only after spinning up the
  thread and emitting the early SSE noise.
- Now `stop_agent()` also removes the user_id from `self._queue`
  under the runner's lock. The action=stop flag still wins for users
  whose hunt is already running.

## Updates
- None.

## Known issues
- Same as a88.
