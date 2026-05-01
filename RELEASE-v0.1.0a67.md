# Huntova v0.1.0a67 — 2026-05-01

## Bug fixes

### Personal-email picker null-checks `validate_email`
- The personal-email branch assigned `validate_email(_personal[0])`
  unconditionally to `lead["contact_email"]`. If validation
  returned None (invalid TLD, disposable domain), `contact_email`
  would be None and downstream code that expected a string crashed.
  The generic-email branch already had the null-check.
- Now: stores result, only assigns + sets confidence on success,
  falls back to the generic branch if the personal candidate
  fails validation.

## Updates
- None.

## Known issues
- Same as a66.
