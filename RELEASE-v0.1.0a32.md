# Huntova v0.1.0a32 — 2026-05-01

When the chat panel said "No AI provider configured", the user's
only recourse was to read the long error message and figure out
either the CLI command or the env var. Now there's a one-tap
button to drop them into Settings → Providers.

## Updates

### Chat "no provider" gets a one-tap CTA
- When `/api/chat` returns the no-provider answer (text starts
  with "No AI provider configured"), the chat panel now appends a
  purple "Open Providers tab →" button below the bot reply.
- Clicking it: closes the chat slideover, opens Settings, and
  switches to the Providers tab in one go. Users can paste an
  Anthropic key and be hunting in seconds.

## Bug fixes
- None.

## Verified live
- ✓ Sent "find me 5 video studios in Berlin" with no provider
  configured
- ✓ Bot replied with the no-provider message + CTA button rendered
  inline beneath
- ✓ Clicking the CTA closes chat + opens Settings → Providers tab

## Known issues
- Same as a31. Nothing new opened.
