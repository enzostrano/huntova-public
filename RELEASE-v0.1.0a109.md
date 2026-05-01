# Huntova v0.1.0a109 — 2026-05-01

## Bug fixes

### `_NOREPLY_RE` covers more no-reply variants
- The regex caught `noreply`, `no-reply`, and `donotreply` (no
  hyphens), but missed the equally common `do-not-reply`,
  `do_not_reply`, `automated@`, and `auto-reply@` patterns. Those
  addresses slipped past `validate_email()` and ended up in
  `contact_email`, so the agent drafted personal cold email to a
  no-reply mailbox that bounces or auto-replies-and-gets-flagged.
- Pattern now includes `do-not-reply`, `do_not_reply`,
  `automated`, `auto-reply` in addition to the previous set.

### `event_type` deduped against `event_name`
- AI sometimes restated the same string for both fields (e.g.
  `event_name: "Women's Conference"`, `event_type: "Women's
  Conference"`). The lead card then rendered the same phrase twice
  separated by `·`, which looked broken.
- After the existing `_to_str` normalization pass, we
  case-insensitively compare the two; if they match, the redundant
  `event_type` is cleared. Type renders empty (or falls through to
  whatever the row template uses as a fallback) instead of echoing
  the name.

## Updates
- None.

## Known issues
- Same as a108.
