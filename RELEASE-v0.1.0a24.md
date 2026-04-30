# Huntova v0.1.0a24 — 2026-05-01

Two friendlier-feel polish wins caught in the live-Playwright audit:
the local user is no longer named "Local User", and the time-of-day
greeting handles late-night correctly.

## Updates

### Local user uses your OS username
- `_ensure_local_user()` in `auth.py` was hardcoding `display_name =
  "Local User"`. The dashboard greeted you with "Good morning, Local
  User!" — robotic.
- Now reads the OS username via `getpass.getuser()` and capitalises
  it. So on macOS the dashboard reads "Working late, Enzomacbook!"
  Falls back to "You" if `getuser()` returns root or fails.
- Existing local users with the legacy "Local User" display_name are
  auto-upgraded on next request. One-time migration; idempotent.

### Late-night greeting
- The greeting band logic was `h<12 ? morning : h<17 ? afternoon : evening`.
  At midnight `h=0 → morning`, which read as "Good morning" at 1am.
- New band: 22:00–04:59 → "Working late". 05:00–11:59 → morning.
  12:00–16:59 → afternoon. 17:00–21:59 → evening.

## Bug fixes
- None — quality-of-life polish.

## Known issues
- `CLAUDE.md` still legacy SaaS spec.
- The dashboard greeting gets your *system* username, not whatever
  you typed in Settings → Profile → Your Name. If you set a custom
  name in Settings, that wins; otherwise the OS username is used.
