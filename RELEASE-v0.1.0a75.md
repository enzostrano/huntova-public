# Huntova v0.1.0a75 — 2026-05-01

## Bug fixes

### `extract_phone_numbers` rejects dates + ZIP+4
- The loose-text regex matched `2024-01-15` (date) and
  `10001-1234` (US ZIP+4). Both have 8-9 digits which passed the
  `7 <= len(digits) <= 15` gate and ended up in `contact_phone`.
- Now: explicit date / ZIP+4 patterns rejected before the digit
  count check. Plus: if the candidate has no `+` country-code
  prefix, require at least 10 digits — 7-9 without `+` is almost
  always an ISBN, internal extension, or date fragment, not a
  real phone. `tel:` links bypass this and keep the 7-digit floor
  since the markup is explicit user intent.

## Updates
- None.

## Known issues
- Same as a74.
