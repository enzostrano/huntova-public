# Huntova v0.1.0a47 — 2026-05-01

Two more agent-found bugs landed. One thread-local fix, one
defensive URL-scheme validation in the bundled webhook plugins.

## Bug fixes

### `app.py:gdpr_erasure` uses thread-local seen_fps if available
- The function rebuilds the `_seen_fps` set after a delete-by-domain.
  It was unconditionally writing the module-level global, which races
  with concurrent agent runs (theoretical at MAX_CONCURRENT_AGENTS=1
  but the codebase already establishes the `ctx = _ctx()` pattern in
  `record_domain_fail` and elsewhere).
- Now: prefer `ctx.seen_fps` if a thread-local context is active;
  fall back to the module global otherwise. Same pattern as
  `record_domain_fail()`.

### `bundled_plugins.py` URL scheme defense (slack-ping + generic-webhook)
- Both plugins fed `url` straight to `urllib.request.urlopen()`. A
  hostile or malformed `ctx.settings.webhook_url` could specify
  `file://`, `smb://`, `gopher://`, etc., and the urllib openers
  honor those — leading to local file reads or network share access.
- Added a scheme check: if the URL doesn't case-insensitively start
  with `http://` or `https://`, the plugin returns silently.
- Local mode is single-user so the threat surface is narrow, but the
  defense is essentially free and closes a foot-gun.

## Updates
- None.

## Known issues
- Same as a46.
