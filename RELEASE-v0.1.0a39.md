# Huntova v0.1.0a39 — 2026-05-01

The CLI's "no API key configured" preflight only checked three env
vars (`HV_GEMINI_KEY`, `HV_ANTHROPIC_KEY`, `HV_OPENAI_KEY`). Users
who'd configured Groq, DeepSeek, OpenRouter, Mistral, Perplexity,
Together, or any local server (Ollama, LM Studio, llamafile) hit a
false "no key" rejection. Fixed.

## Bug fixes

### `huntova hunt` no-key check uses `list_available_providers()`
- `cmd_hunt` was rejecting users with valid keys for any of the 10
  non-headline providers. Switched to
  `providers.list_available_providers()` which checks env + keychain
  + config.toml across all 13 supported providers.
- Falls back to the env-only check if `providers` import fails
  (preserves the old behavior in degraded environments).

### `huntova serve` startup heads-up same fix
- The CLI message "no API key configured — run `huntova onboard`
  first or set HV_ANTHROPIC_KEY / HV_GEMINI_KEY / HV_OPENAI_KEY"
  was only checking those three vars. Now uses
  `list_available_providers()` so a user with `HV_GROQ_KEY` set
  doesn't get told they have no key.

## Updates
- None.

## Known issues
- Same as a38.
