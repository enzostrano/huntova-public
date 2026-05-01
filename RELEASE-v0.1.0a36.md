# Huntova v0.1.0a36 — 2026-05-01

The chat slideover finally drives the start popup end-to-end.
"find me 5 video studios in Berlin and Paris" now opens the popup
with Germany + France pre-selected, max-leads = 5, and the user
just clicks Start.

## Updates

### Chat → start-popup pre-fill
- Chat dispatcher (`/api/chat` + `hvChatSend()`) used to open the
  start popup with default countries when the chat returned a
  `start_hunt` action — defeating the whole "AI understood the
  request" win.
- Frontend now: when `action==='start_hunt'`, sets every country in
  `_startCountries` to false, then activates the suggested ones
  (case-insensitive lookup). If none match the dashboard's known
  list, falls back to defaults so we don't open empty.
- Plus pre-fills the `Max leads this hunt` input from `d.max_leads`
  AFTER `openStartPopup()` (which resets the input on each open).
- Summary line updates to "N countries selected" via
  `updateStartSummary()`.

### Chat system prompt: full English country names
- Was: ISO codes (`["DE"]`).
- Now: full names (`["Germany"]`). Matches the dashboard's country
  list directly so no ISO-to-name mapping is needed.

## Bug fixes
- None new.

## Verified live (Playwright, stubbed /api/chat)
- ✓ Chat returns `{action:'start_hunt', countries:['Germany','France'],
  max_leads:5}` → start popup opens with **only** Germany + France
  highlighted, max-leads input shows "5", summary reads "2 countries
  selected"

## Known issues
- Same as a35.
