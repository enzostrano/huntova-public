# Huntova v0.1.0a56 — 2026-05-01

**Real CRM duplication bug fixed.** When the agent re-encountered a
lead it had already saved (via the dedup path), the post-save flow
ran in full anyway: pushed the lead onto `ctx.all_leads` again,
re-emitted the `lead` SSE event, re-fired post_save plugins
(csv-sink, slack-ping, generic-webhook). User saw duplicate rows in
the CRM and got a duplicate Slack ping.

## Bug fixes

### `app.py` refreshed-lead path skips re-emit + re-plugin
- Was: the `_lead_saved and not _was_new` branch only logged
  "↻ Refreshed existing lead" then fell through into the
  `_ctx().all_leads.append(lead)` block. Same lead got pushed
  into the in-memory list every time the agent rediscovered it.
- Now: the refresh branch emits a `crm_refresh` SSE so the UI
  re-reads from DB (in case any field changed) then `continue`s
  past the new-lead emit/save/plugin block. New leads still get
  the full path; refreshed ones get just the CRM nudge.

## Updates
- None.

## Known issues
- Same as a55.
