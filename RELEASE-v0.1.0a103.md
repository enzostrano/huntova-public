# Huntova v0.1.0a103 — 2026-05-01

## Bug fixes

### Team-page role extraction strips leading articles + trailing punctuation
- The `enrich_contact()` regex captures roles like `Head of Sales`,
  `Director of Operations`, `VP of Marketing`. When the source HTML
  embedded the phrase mid-sentence ("...the Head of Sales is..."),
  the captured group came back as `"the head of sales"` (article
  intact) or `"director of operations,"` (trailing comma). That
  string then landed in `contact_role` and surfaced verbatim in
  email personalisation — `"Hi Jane, as the head of sales,…"`
  reads odd.
- Added a small post-process: strip a leading `the|a|an` (case-
  insensitive) and trim trailing `.,;:` punctuation before the
  80-char cap.

## Updates
- None.

## Known issues
- Same as a102.
