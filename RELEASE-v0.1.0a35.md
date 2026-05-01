# Huntova v0.1.0a35 — 2026-05-01

Anthropic Claude is the BYOK default per the project rules
(CLAUDE.md rule #11), but two user-facing strings still listed
Gemini first. Reordered.

## Bug fixes

### `providers.py` "no API key" error message order
- Was: "set HV_GEMINI_KEY (or HV_ANTHROPIC_KEY / HV_OPENAI_KEY)…"
- Now: "set HV_ANTHROPIC_KEY (default), HV_OPENAI_KEY, or HV_GEMINI_KEY…"
- Matches `_DEFAULT_ORDER` which already lists Anthropic first.

### `huntova init` next-steps order
- The "next step — set ONE of these in your env:" block was
  printing HV_GEMINI_KEY first.
- Reordered: Anthropic first (with "(default)" suffix), then OpenAI,
  then Gemini.

## Updates
- None.

## Verified
- Both strings now lead with Anthropic. Behavior unchanged (only copy
  order); resolves a Gemini-bias inconsistency Enzo's audit caught.

## Known issues
- Same as a34 (cloud-side telemetry_opt_in still not consulted).
