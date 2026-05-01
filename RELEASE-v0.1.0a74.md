# Huntova v0.1.0a74 — 2026-05-01

## Bug fixes

### Name-extractor strips role + suffix noise
- `guess_email_patterns` consumed the captured "name" verbatim. The
  regex above grabs 2-3 capitalised words on a role-line, so phrases
  like "Director John Smith" captured as "Director John" or
  "John Smith Jr" → email guesser built garbage like
  `john.jr@domain.com`, `director.john@domain.com`.
- Now: a `_NAME_NOISE` set (`Jr Sr II III IV PhD MD Director Manager
  CEO Founder ...`) strips suffix/title tokens. Only entries with
  ≥2 clean tokens land in `all_names`. The downstream pattern
  guesser sees real first/last names.

## Updates
- None.

## Known issues
- Same as a73.
