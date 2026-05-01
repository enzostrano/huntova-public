# Huntova v0.1.0a77 — 2026-05-01

## Bug fixes

### `is_recurring` AI-prompt guard against false positives
- The AI scoring schema asked for `is_recurring: true/false` but
  the prompt didn't tell the model to ignore "annual" used in
  contexts like "annual report" or "annual meeting" — a one-off
  document or event that happens yearly is not a recurring service
  signal. Result: investor-relations pages with "Annual Report
  2025" were getting `is_recurring=true`, which then biased the
  fit score and email-drafting toward "they need ongoing service".
- Added an explicit rule before the JSON schema: TRUE only for
  repeating service needs (subscriptions, monthly retainers,
  annual service contracts). One-off events / documents do not
  count.

## Updates
- None.

## Known issues
- Same as a76.
