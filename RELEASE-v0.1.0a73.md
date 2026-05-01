# Huntova v0.1.0a73 — 2026-05-01

## Bug fixes

### Lead region mapping: UK + USA aliases
- Schema enum is `["EU","USA","Middle East","UK","Other"]` but the
  region-fixer only mapped EU + USA + Middle East variants. UK,
  England, Scotland, Wales, Northern Ireland leads landed in the
  catch-all "Other" bucket — broke the dashboard's regional sort
  for UK prospects.
- Now: explicit branch maps "United Kingdom", "UK", "England",
  "Scotland", "Wales", "Northern Ireland" → region="UK".
- Also added "USA" alias to the United States branch (some leads
  store it as the abbreviation).

## Updates
- None.

## Known issues
- Same as a72.
