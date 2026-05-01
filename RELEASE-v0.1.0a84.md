# Huntova v0.1.0a84 — 2026-05-01

## Bug fixes

### `/api/track-actions` whitelists action type
- Endpoint accepted any string for `action`. Garbage names from a
  hostile or buggy client polluted the `lead_actions` table and
  broke `GROUP BY action_type` analytics.
- Now: 18-entry whitelist (click/open/view/csv_export/rewrite/etc).
  Unknown action names map to `"other"` so the row is still
  tracked (volume signal preserved) without polluting the
  GROUP-BY surface. Plus length cap of 40 chars.

### JSON-LD `person_types` includes AboutPage + ContactPage
- `_extract_jsonld_item` only recognised `Person` and `ProfilePage`.
  Many company sites wrap their team / about / contact pages in
  `AboutPage` or `ContactPage` JSON-LD blocks; those were silently
  skipped, missing names + emails the agent could have extracted.
- Added both to the tuple.

## Updates
- None.

## Known issues
- Same as a83.
