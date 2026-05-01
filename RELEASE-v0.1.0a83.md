# Huntova v0.1.0a83 — 2026-05-01

Three agent-found bugs from the parallel-launch wave.

## Bug fixes

### `/auth/signup` normalises email to lowercase
- Login + forgot-password + reset-password all `.lower()` before
  lookup, but signup kept case as-is. Signing up with
  `Test@Example.com` then trying password reset as
  `test@example.com` failed because the reset token was bound to
  the case-preserved signup email and the lookup paths all
  lowercased.
- Now: signup normalises like the rest of the auth flow.

### Stripe webhook rejects events without `type`
- `event.get("type", "")` defaulted to empty string. Malformed
  events with no type silently fell through every dispatch check
  with no error returned to Stripe — so retries never came.
- Now: explicit reject with 400 + `Webhook event missing type`.
  Stripe retries with the correct payload (or surfaces a real
  parsing bug we own).

### `is_user_blocked` Unicode-normalises org names
- Org-name comparison did `.lower().strip()` but no Unicode
  normalisation. A blocklist entry stored as NFC ("Café") could
  silently mismatch a prospect delivered as NFD ("Cafe<combining
  acute>") from a source API that emits decomposed form. Block
  bypass for any accented org name.
- Now: both sides normalised to NFC before compare.

## Updates
- None.

## Known issues
- Same as a82.
