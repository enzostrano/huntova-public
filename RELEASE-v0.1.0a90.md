# Huntova v0.1.0a90 — 2026-05-01

## Bug fixes

### Scoring prompt caps `HUNTOVA_CONTEXT` at 12 KB
- The page-text / snippet / scoring-rules part of the lead-scoring
  prompt is already bounded (`page_text[:_page_limit]`), but the
  `HUNTOVA_CONTEXT` block prepended to every call wasn't. Long-time
  users with a heavily-trained wizard (full company brief + ICP +
  archetype + accumulated `_knowledge` + 30 +-line learning summary)
  could push the prefix past 30 KB, eating into the provider's
  output budget and inflating per-call cost.
- Now a hard cap of 12 000 chars with a "[…context truncated for
  token budget…]" trailer so the model knows the cut happened.

### `/api/teach` clamps `instruction_summary` at 2 KB
- `save_lead_feedback` writes whatever the AI returns under
  `instruction_summary` straight into the `learning_profile` row.
  A miscalibrated provider (or a prompt-injected page) can return
  multi-KB text, which then bloats the row + every subsequent
  `_build_ai_context()` call. We now coerce non-string to `""` and
  slice to 2 000 chars before persisting.

## Updates
- None.

## Known issues
- Same as a89.
