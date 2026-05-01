# Huntova v0.1.0a108 — 2026-05-01

## Bug fixes

### `validate_email` accepts the `Name <email>` capture form
- JSON-LD `ContactPoint` blocks and some structured-data crawls
  surface emails inline with the contact's display name —
  `Jane Doe <jane@acme.com>`. The previous validator lowercased and
  ran the regex on the whole string, so the angle-bracket form
  failed the pure-`local@domain` regex and was silently dropped.
  Real contact data the agent had already extracted disappeared
  before the lead row landed.
- Now if the value contains `<…>`, we extract whatever sits between
  the *last* `<` and the *last* `>` and lowercase that. Plain
  `local@domain` strings still go through the same path.

### `huntova benchmark` only targets configured providers
- When the user hadn't passed `--provider`, the benchmark fell
  through to `_DEFAULT_ORDER` (a hardcoded list) whenever
  `list_available_providers()` returned empty. That ran the
  benchmark against providers the user never set up — they got a
  table of `auth_failed` rows for keys they don't own and assumed
  the tool was broken.
- Removed the `_DEFAULT_ORDER` fallback. With no configured
  providers, the existing "no providers configured — run
  `huntova onboard` first" message fires, which is the actual
  desired behaviour.

## Updates
- None.

## Known issues
- Same as a107.
