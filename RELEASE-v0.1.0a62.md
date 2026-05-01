# Huntova v0.1.0a62 — 2026-05-01

Two more agent-found bugs.

## Bug fixes

### `db.py:create_hunt_share` retries on PK collision
- 8-byte (64-bit) slug + a fresh `secrets.token_urlsafe(8)` makes
  collisions essentially impossible in production. But fixture
  re-use in tests + edge-case race conditions could still hit a
  PRIMARY KEY violation, crashing the whole share-create request.
- Now: 3-attempt retry loop. Each attempt regenerates a fresh
  slug. Only retries on UNIQUE / PRIMARY KEY error patterns; other
  errors (NotNull, FK violation) re-raise immediately.

### `app.py:emit_lead` strips underscore-prefixed internal fields
- The agent attaches state to leads (`_contact_source`,
  `_contact_confidence`, `_guessed_emails`, `_social_twitter`,
  `_full_text`, `_site_text`) that's used internally during
  scoring/enrichment but isn't part of the public LEAD_SCHEMA
  (`"additionalProperties": false`).
- These fields used to flow over SSE to the dashboard, fattening
  the frame size + leaking agent-side debug info to the client.
- Now: `emit_lead` filters to public fields only by dropping
  any key starting with `_`. Defense-in-depth — the same
  filtering happens at the share-publishing layer (`_sanitise_lead_for_share`)
  but emit was the gap.

## Updates
- None.

## Known issues
- Same as a61.
