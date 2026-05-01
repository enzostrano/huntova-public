# Huntova v0.1.0a64 — 2026-05-01

## Bug fixes

### `.nav-btn:focus-visible` parity with hover
- `.nav-btn` had only `:hover` styling. Keyboard users got the
  generic outline only — visually weaker than the hover treatment
  mouse users see. Hurts keyboard-nav legibility especially on
  `<900px` where the topnav is the primary nav (sidebar drawer).
- Added a matching `:focus-visible` rule with the same background
  + color as `:hover`.

### `cli.py:_dispatch_hunt` country sanitisation
- The chat REPL's `start_hunt` action accepted any string the AI
  put in `countries[]`. A hallucinated entry containing commas
  would break the comma-joined `--countries` flag downstream,
  feeding garbage to `cmd_hunt`. Plus no length cap — the AI could
  emit a 50-entry list each 200 chars long.
- Now: cap list to 30 entries, truncate each to 50 chars, drop
  entries containing commas or control characters. No whitelist —
  free-form geo names like "Northern California" or "Benelux"
  still work, but obvious garbage gets filtered.

## Updates
- None.

## Known issues
- Same as a63.
