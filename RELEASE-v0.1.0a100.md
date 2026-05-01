# Huntova v0.1.0a100 — 2026-05-01

## Bug fixes

### `_hostish_netloc` strips an explicit port before returning the host
- A URL with an explicit port (e.g. `https://google.com:443/contact`)
  came back as `google.com:443`. The downstream `is_blocked()` check
  compares the result against `MEGA_CORP_DOMAINS` and a per-user
  blocklist via exact equality + suffix match. Because
  `"google.com:443" != "google.com"` and doesn't end in `.google.com`
  either, blocked-domain entries silently let the URL through.
- Now slices off whatever follows the colon so `google.com:443` →
  `google.com` before the comparison. Schemeless inputs and
  `www.` prefix handling work the same as before.

## Updates
- None.

## Known issues
- Same as a99.
