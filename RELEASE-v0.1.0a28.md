# Huntova v0.1.0a28 — 2026-05-01

Caught a real pre-existing bug in `auth.py` while running a mobile-
viewport audit. `os` was used in the verbose-log path without being
imported — when `_ensure_local_user()` raised any exception, the
fallback log path crashed with `NameError`, masking the real
underlying failure with a NameError-induced 500.

## Bug fixes

### `auth.py` missing `import os`
- `get_current_user()` had `if os.environ.get("HV_VERBOSE_LOGS"):`
  in the exception handler at line 287. `os` was never imported,
  so any exception in `_ensure_local_user` produced a `NameError`
  500 instead of the real error message.
- Added `import os` at the top of `auth.py`. Now setting
  `HV_VERBOSE_LOGS=1` actually prints the bootstrap error if one
  occurs, and `_ensure_local_user` returns `None` cleanly otherwise
  (which the caller handles).

## Verified live (mobile viewport 375×812)
- ✓ Sidebar hides at <900px, topnav-centre takes over
- ✓ Onboard banner stacks correctly
- ✓ "Working late, Enzomacbook!" greeting reads well on mobile
- ✓ Quick-action cards stack vertically
- ✓ Stat cards display in 2-column grid
- ✓ No console errors after the auth fix

## Known issues
- Mobile sidebar drawer (full hamburger UI) still TODO — current
  fallback uses topnav-centre, which is functional.
- Cloud-side `telemetry_opt_in` still not consulted by backend.
