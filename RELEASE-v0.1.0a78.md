# Huntova v0.1.0a78 — 2026-05-01

## Bug fixes

### `emit_browsing_state` strips tracking params from live-view URLs
- The dashboard live-view feed showed raw prospect URLs including
  `utm_*`, `fbclid`, `gclid`, `msclkid` and similar tracking
  tokens. Marketing-campaign URLs sometimes pack hashed-email
  identifiers into those, so the feed could surface PII in the UI.
- Now: route through the existing `normalize_url()` helper (already
  used for lead dedup) before emitting. Fallback to raw URL if
  normalisation throws — feed never breaks because of a parser
  hiccup.

## Updates
- None.

## Known issues
- Same as a77.
