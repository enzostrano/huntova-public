# Huntova v0.1.0a43 — 2026-05-01

**Plugin pipeline broken pipeline fix.** The agent's HookContext
was passing `_wiz_data` (just the wizard nested object) as `settings`
instead of the full user_settings dict. Plugins like csv-sink and
slack-ping read top-level keys (`plugin_csv_sink_path`, slack
webhook URL) — these were never visible, so plugins silently no-op'd
even when the user had them configured in Settings → Plugins.

## Bug fixes

### Plugins receive full user_settings, not just wizard data
- Three callsites in `app.py` build `HookContext(settings=_wiz_data ...)`:
  - `pre_search` hook (line 6850)
  - `post_search` hook (line 7070)
  - `post_save` hook (line 7843)
- All three now use `settings=load_settings() or {}` which returns the
  merged DEFAULT_SETTINGS + user_settings — including all top-level
  keys like `plugin_csv_sink_path`, `webhook_url`, `dedup`,
  `slack_ping`, `csv_sink`, `smtp_host`/`smtp_user`/`smtp_port`.

## Affected plugins (now actually work when configured)
- `csv-sink` — reads `plugin_csv_sink_path` (or nested `csv_sink.path`).
- `slack-ping` — reads nested `slack_ping.webhook_url` (and the
  HV_SLACK_WEBHOOK_URL env, which secrets_store hydrates).
- `dedup-by-domain` — reads nested `dedup.window_days`. It accidentally
  worked because the `_wiz_data` shape happened to not include `dedup`
  but that key never existed before the dashboard either.
- `recipe-adapter` and `adaptation-rules` are env-var-driven (not
  settings-driven), so they were unaffected.

## Updates
- None.

## Known issues
- Same as a42.
