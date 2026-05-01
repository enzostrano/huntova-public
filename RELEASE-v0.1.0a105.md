# Huntova v0.1.0a105 — 2026-05-01

## Bug fixes

### `is_recurring` / `is_virtual_only` coerced from real boolean semantics
- `bool(data.get("is_recurring", False))` is fine when the AI returns
  the JSON literals `true` / `false`. But when a misbehaving model
  emits the *string* `"false"`, `bool("false")` is **`True`**
  (any non-empty string), so a one-shot conference got marked
  recurring and slipped past the past-date filter as a "future"
  occurrence.
- Replaced both with a `_coerce_bool` helper that accepts
  `True/False`, `1/0`, and the strings `"true"/"yes"/"1"`
  (case-insensitive). Anything else falls back to `False`.

## Updates
- None.

## Known issues
- Same as a104.
