# Huntova v0.1.0a70 — 2026-05-01

70th release. Two more agent-found bugs.

## Bug fixes

### `validate_email` rejects common-typo TLDs
- The email regex `[a-zA-Z]{2,}` accepted any 2+ letter TLD —
  including common keyboard-fumbles like `.con`, `.cmo`, `.vom`,
  `.ner`, etc. Crawler-extracted emails with these typos slipped
  into the CRM and burned enrichment cycles.
- Now: `_TYPO_TLDS` set lists the common typos and validate_email
  rejects them before returning.

### `.dash-big` stat number overflow defense
- Large stat numbers (`12345`, `67890%`) on the narrow mobile
  stat-card layout (~240px wide) overflowed without truncation.
  `.stat-card` had `overflow:hidden` but the number itself had no
  fallback — it'd clip at the right edge with no ellipsis.
- Added `overflow:hidden; text-overflow:ellipsis; max-width:100%;
  white-space:nowrap` to `.dash-big`.

## Updates
- None.

## Known issues
- Same as a69.
