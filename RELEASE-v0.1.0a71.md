# Huntova v0.1.0a71 — 2026-05-01

## Bug fixes

### `huntova share status` now surfaces revoked state
- `/api/share/{slug}/views` returned `views_30d` for any slug
  regardless of whether the share had been revoked. The CLI's
  `share status` printed the count without indicating the share
  was dead — confusing UX.
- Server: endpoint now reads `hunt_shares.revoked` first, returns
  404 for non-existent slugs, includes `revoked: bool` in the
  response payload. View count still returned (audit retention).
- CLI: `cmd_share status` reads `revoked` and prints a red
  `status: REVOKED` line when set, with a note that old view
  counts are retained for audit but the public link returns 410.

## Updates
- None.

## Known issues
- Same as a70.
