# Huntova v0.1.0a20 — 2026-04-30

Round-2 of the SaaS leftover audit. Adds an in-dashboard Providers
tab so users no longer have to eject to `/setup` to swap an API key.
Also kills three more SaaS-mode-only code paths that were running
in local mode and producing nonsense "out of credits" gates.

## Updates

### Providers tab inside Settings (no `/setup` eject)
- New `Providers` vtab in Settings, gated `hv-local-only` (hidden
  in cloud mode where keys come from env vars).
- Lists every supported provider (Anthropic default, OpenAI, Gemini,
  OpenRouter, Groq, DeepSeek, Together, Mistral, Perplexity, Ollama,
  LM Studio, llamafile) with current status (✓ Configured / Not set).
- Per-row Save & test button posts to existing `/api/setup/key`,
  which writes to OS keychain and runs a 1-shot probe. Status pill
  refreshes after save.
- "Get key ↗" deeplink to each provider's key page.
- Bottom of tab still has an "Open Auto Wizard ↗" CTA for users who
  want the guided 90-second flow.

### `hv-local-only` gating class
- New CSS class wired in `hvApplyRuntime()`. Hides any element when
  `_hvRuntime.mode !== 'local'`. Mirrors the existing trio of
  `hv-saas-only`, `hv-billing-only`, `hv-auth-only`.

## Bug fixes

### "Account / Data" tab now reads "Data" in local mode
- Tab label split: `<span class="hv-saas-only">Account / </span>Data`.
  Local mode hides the prefix; cloud mode shows the full label.
- Inside the tab: "every lead matching your account email" → "every
  lead in your local database" (no account in local).

### Hunt-launch credit gate skips local mode
- `launchAgent()` was checking `_hvAccount.credits_remaining<=0` and
  calling `hvOpenPricing()` even in local mode. Wrapped the check
  in `if (_hvRuntime.billing_enabled)`.
- `startResearch()` (Deep Research) fix: same gate, same wrap.
- `updateStartSummary()` start-popup CTA: in local / BYOK mode it
  now reads "Runs until stopped. API spend is on your provider."
  No tier name, no leads-remaining counter, no upgrade CTA.

### Server-error toast no longer opens pricing modal in local
- `/agent/control` POST error handler matched any error message
  containing "credit" or "upgrade" and auto-opened pricing. Wrapped
  in `_hvRuntime.billing_enabled` so local users just see the toast.

### `credits_exhausted` SSE event ignored in local mode
- Listener was unconditionally calling `hvLoadAccount()` +
  `hvTokenPopup('empty')`. Now early-returns when billing is off.

### Public share modal copy genericized
- "Recipients don't need a Huntova account." → "Local mode keeps the
  file on your machine; cloud mode publishes a shareable URL."
- "You can revoke the link anytime from your account." → "You can
  delete the snapshot anytime."

## Known issues
- `CLAUDE.md` is still the legacy SaaS spec (PostgreSQL, Stripe,
  Render, OAuth) — needs a full rewrite to reflect the local-first
  CLI shape. Tracking for a21.
- Mobile sidebar drawer (<900px) still TODO.
