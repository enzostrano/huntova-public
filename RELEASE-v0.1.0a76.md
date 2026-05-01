# Huntova v0.1.0a76 — 2026-05-01

## Bug fixes

### Notes textarea + button defense-in-depth `esc(id)`
- Lead modal's notes textarea (`<textarea id="mn-{id}">`) and Save
  button (`onclick="saveModalNotes('{id}')"`) inlined the lead_id
  unescaped. Lead IDs are currently 12-char hex hashes so quotes
  can't appear in practice — but if the ID generation ever
  changes (longer salts, custom format), an injected quote would
  break the onclick string and silently disable Save Notes.
- Wrapped both in `esc(id)`. Mirrors the pattern already used in
  the lead-detail page version (`renderRow`).

## Updates
- None.

## Known issues
- Same as a75.
