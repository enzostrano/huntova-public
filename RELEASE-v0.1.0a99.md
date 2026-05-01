# Huntova v0.1.0a99 — 2026-05-01

## Bug fixes

### Free-form summary fields capped to 240 chars
- The lead-row column shows `why_fit`, `evidence_quote`, and
  `production_gap` in a fixed-width slot. The AI sometimes returns
  multi-sentence essays for these fields (especially for the
  one-sentence `why_fit` "rationale" — the spec says one sentence,
  but the model occasionally writes a paragraph). The row layout
  blew out, the inline rationale wrapped onto 5+ lines, and the
  status pill on the right slipped off the visible viewport.
- Capped each of the three at 240 chars after the existing
  `_to_str()` normalization. The longer narrative version is still
  available in the underlying lead JSON — only the UI string is
  trimmed.

## Updates
- None.

## Known issues
- Same as a98.
