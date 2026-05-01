# Huntova v0.1.0a98 — 2026-05-01

## Bug fixes

### Inline-edit save preserves an explicitly-cleared subject / body
- The lead-detail modal's autosave fetch built its body with
  `ss || currentModalLead.email_subject` (and the body equivalent).
  When a user *cleared* the subject (or body) to an empty string,
  the `||` shortcut treated `""` as falsy and fell back to the
  cached old value — so the cleared field never reached the server,
  and reopening the modal showed the previous text.
- Switched to an explicit `null/undefined` check (`!==null && !==undefined ? value : cached`). Empty strings now travel
  through and the server stores the cleared field. Anything actually
  unset (modal closed without typing) still falls back to the cache.

## Updates
- None.

## Known issues
- Same as a97.
