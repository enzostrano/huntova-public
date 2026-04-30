# Huntova v0.1.0a21 — 2026-04-30

Round-3 of the SaaS leftover audit. The "Credits Left" stat card on
the dashboard finally hides in local mode and a "Providers" card
takes its place. Plus three more credit-cost UI strings localized.

## Updates

### Dashboard "Credits Left" → "Providers" in local
- The big "Credits Left" stat card on `/` was running unconditionally
  in local mode — gating bug from the original v6 dashboard.
- Wrapped the card in `hv-billing-only` (hides in local).
- New `hv-local-only` "Providers" card takes its place. Shows count
  of configured providers (live from `/api/setup/status`) with a
  helper sub-label: "None configured — open Settings" or "N active".

### Deep Research cost label localized
- `(1 credit)` pill on the Deep Research button now reads
  `(~$0.04 API spend)` in local mode.
- Confirm dialog wording adjusted: "Costs ~$0.04 of API spend on
  your provider" instead of "Deep Research costs 1 credit."

### `neoTips` rotator filtered for local
- The 14-tip rotator at the bottom of the dashboard had three
  cloud-specific tips ("Top-up credits never expire", "Setting the
  right tier helps forecast your pipeline", "Your pipeline value
  updates live in the Dashboard").
- In local mode the rotator strips those + appends 5 BYOK-relevant
  tips: `huntova benchmark` reminder, Claude default + provider
  switch hint, plugin pointer, `huntova teach` pointer, and a "back
  up your `huntova.db`" reminder.

## Bug fixes
- None (all gating wins, no regressions found).

## Known issues
- `CLAUDE.md` still legacy SaaS spec (a22+).
- Mobile sidebar drawer still TODO.
- Some plugin/recipe modals still SaaS-styled when opened in local
  mode (cosmetic — they work fine).
