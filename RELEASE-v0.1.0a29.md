# Huntova v0.1.0a29 — 2026-05-01

Caught a sidebar mislabel: "Recipes" pointed to `/demo` (a sample
hunt page), not anything recipe-related. Renamed to "Sample hunt"
to match what the link actually opens.

## Bug fixes

### Sidebar "Recipes" → "Sample hunt"
- The sidebar's AGENT section had a row labeled "Recipes" that
  opened `/demo` — Huntova's public sample-hunt page (Aurora Studios,
  Tessera Marketing, Helio Production). Confusing if the user
  expected a recipe browser.
- Renamed the link text to "Sample hunt". The CLI's `huntova
  recipe` subcommands are still where actual recipe management lives.

## Updates
- None.

## Known issues
- Same as a28.
