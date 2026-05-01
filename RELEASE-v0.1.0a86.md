# Huntova v0.1.0a86 — 2026-05-01

## Bug fixes

### Country name normalised before EU/UK/USA region assignment
- `eu_countries` is a title-case set (`{"France", "Germany", ...}`).
  When the AI returned `"france"` / `"FRANCE"` / `"United states"`,
  the membership check missed and `region` defaulted to `"Other"`.
  That broke the region pill on the dashboard + the geo filter on
  the leads list.
- Now title-cases the AI-emitted country first, with explicit
  aliases for the all-caps acronyms (`USA`, `EU`, `UAE`, `UK`).

### Landing-page install one-liner uses an explicit `https://` scheme
- `curl -fsSL huntova.com/install.sh | sh` works through curl's
  default-scheme + `-L` redirect handling, but the security-conscious
  bug was clear: a copy-paste from the page should never depend on
  redirect behaviour. The install command now reads
  `curl -fsSL https://huntova.com/install.sh | sh`.

## Updates
- None.

## Known issues
- Same as a85.
