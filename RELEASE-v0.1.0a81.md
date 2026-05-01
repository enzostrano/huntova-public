# Huntova v0.1.0a81 — 2026-05-01

## Bug fixes

### `/api/lead-feedback` DNA refinement only fires on bucket-crossings
- `should_refine = total > 0 and total % 10 == 0` fired on every
  save where `total` was a multiple of 10. With UPSERT-based
  feedback (re-feedback on the same lead_id leaves the total
  unchanged), the refine would re-trigger every time the user
  updated an existing feedback at total=10, 20, 30 — burning AI
  budget on duplicate DNA generation calls.
- Now: snapshot the count BEFORE the save and compare the
  pre/post 10-bucket: `(pre_total // 10) < (post_total // 10)`.
  Catches the genuine 9→10 crossing, suppresses the 10→10 UPSERT
  case.

## Updates
- None.

## Known issues
- Same as a80.
