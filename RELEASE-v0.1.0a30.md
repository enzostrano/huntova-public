# Huntova v0.1.0a30 — 2026-05-01

The mobile sidebar drawer that has been TODO across multiple
releases finally ships. Phones get a real hamburger → slide-in
drawer experience with backdrop dismiss + ESC to close + auto-close
on nav.

## Updates

### Mobile sidebar drawer (<900px)
- New `<button class="hv-burger">` in the topnav, visible only at
  <900px. SVG hamburger icon, tap target 36×36.
- Tapping the hamburger calls `hvSidebarToggle()` which adds `.open`
  to the sidebar + backdrop, sets body `overflow:hidden` so the
  page behind doesn't scroll, and arms an ESC keydown handler.
- The drawer is 280px wide (84vw cap), slides in from the left with
  a 0.25s ease-out transform + 8px box-shadow lifts it off the
  page.
- Backdrop is `rgba(0,0,0,.55)`, fades in over 0.2s. Click anywhere
  on it → `hvSidebarClose()`.
- Auto-close on nav: any click inside `.hv-sidebar .hv-sb-row`
  dismisses the drawer (delegated listener + `setTimeout(..., 0)`
  so the row's own onclick fires first).

### Sidebar inner styles moved out of desktop @media
- Was: `.hv-sb-brand`, `.hv-sb-row`, SVG sizing rules etc. were all
  scoped inside `@media(min-width:900px)`. The mobile drawer
  inherited none of them — at <900px the SVGs rendered at viewBox
  size (no width/height set) and rows had no padding/typography.
- Now: structural styles apply at every viewport (mobile drawer
  + desktop inline both look right). The `@media` block only
  handles position/visibility differences.

### Z-index hierarchy normalised
- Topnav was at z-index 100, drawer at 60, backdrop at 55. On
  mobile the topnav floated above the drawer.
- Bumped: backdrop 55 → 105, drawer 60 → 110. Drawer now correctly
  covers the topnav on mobile.

## Bug fixes
- (Same release as the new feature — no separate fixes.)

## Verified live (mobile 375×812 + desktop 1280×900)
- ✓ Hamburger visible only at <900px
- ✓ Drawer slides in with backdrop, all sections render with
  correct spacing/sizing
- ✓ Active row highlights correctly (Overview purple)
- ✓ Auto-close on nav-row click + page switches behind
- ✓ Desktop sidebar still inline 240px, no regressions
- ✓ "Sample hunt" label (a29 carry-over)

## Known issues
- Cloud-side `telemetry_opt_in` flag still not consulted by backend.
- Pre-existing CRM concurrent-update race (cloud only).
