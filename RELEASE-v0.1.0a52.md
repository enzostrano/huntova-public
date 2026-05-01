# Huntova v0.1.0a52 — 2026-05-01

CSS-only fix: wizard chrome was unusable in light mode.

## Bug fixes

### `.iwiz-close` + `.iwiz-textarea` + `.iwiz-pill` light-mode overrides
- The Immersive Wizard ("Train AI" → wizReopen) close button,
  textarea, and pill buttons hardcoded `rgba(255,255,255,.X)`
  backgrounds + borders — designed for dark mode. Under the
  `.light` class they were near-invisible (white-on-white).
- Added `.light .iwiz-*` overrides that flip to dark-on-light
  semantics (`rgba(0,0,20,.X)`).

## Updates
- None.

## Known issues
- Same as a51.
