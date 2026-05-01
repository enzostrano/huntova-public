# Huntova v0.1.0a44 — 2026-05-01

**Phantom feature → real feature.** Settings → Webhooks tab let
users configure a webhook URL + HMAC secret. The "Test webhook"
button worked. But nothing on the agent path read the saved URL —
the saved config was a dead-letter dropbox. New `generic-webhook`
bundled plugin closes the loop.

## Updates

### `generic-webhook` bundled plugin
- New `GenericWebhookPlugin` in `bundled_plugins.py`. Reads
  top-level `webhook_url` from user_settings (or `HV_WEBHOOK_URL`
  env). HMAC-signs payloads with `HV_WEBHOOK_SECRET` from the OS
  keychain (or env fallback) → `X-Huntova-Signature: sha256=<hex>`.
- Fires on `post_save`, same hook as csv-sink + slack-ping.
- Payload shape: `{"event": "post_save", "lead": {...}, "ts": int}`.
  Strips `rewrite_history`, `_full_text`, `_site_text` so receivers
  don't get hundreds-of-KB blobs.
- Soft-fails on HTTP errors — never breaks a hunt because the
  user's receiver returned 500.

### Plumbing
- Plugin added to `_BUNDLED_CLASSES` so `register_bundled` picks
  it up automatically.
- Server-side `_ALLOWED_PLUGINS` set updated so the dashboard
  toggle persists (otherwise the toggle would be silently
  filtered out at save).
- Dashboard `_BUNDLED_PLUGINS` list shows it in Settings → Plugins
  with a description that calls out the HMAC support.

## Bug fixes
- Closes the gap between Settings → Webhooks and the agent
  pipeline. Before a44, configuring a webhook URL in the dashboard
  was effectively a no-op.

## Known issues
- Same as a43.
