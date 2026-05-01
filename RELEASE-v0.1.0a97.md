# Huntova v0.1.0a97 — 2026-05-01

## Bug fixes

### `/api/rewrite` whitelists the `tone` parameter
- The endpoint accepted whatever string the client sent for `tone`
  and passed it straight to `generate_tone_email()`, which used a
  `.get(tone, default)` fallback. The fallback masked the bug — but
  meant any free-form string showed up in the prompt the agent saw.
  A malicious or accidental tone like `"ignore all previous"` got
  embedded in the rewrite system prompt, opening a small
  prompt-injection surface and polluting any downstream telemetry
  that grouped by `last_tone`.
- Now whitelists against the supported set (`friendly`,
  `consultative`, `broadcast`, `warm`, `formal`); unknown tones
  fall back to `friendly` silently.

## Updates
- None.

## Known issues
- Same as a96.
