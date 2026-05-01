# Huntova v0.1.0a33 — 2026-05-01

ESC now closes the chat slideover. Was: keyboard users had to tab
to the X button to dismiss it.

## Updates

### Chat panel ESC-to-close
- `hvChatToggle()` arms a keydown listener when the panel opens
  and removes it on close. ESC anywhere on the page closes the
  panel. Mirrors the same pattern used for the mobile sidebar
  drawer (a30) and the wizard.
- Listener cleanup on close so we don't leak handlers.

## Bug fixes
- None new.

## Verified live (Playwright)
- ✓ Open chat → press ESC → chat closes
- ✓ Listener removed after close (no double-fire on next open)

## Known issues
- Same as a32.
