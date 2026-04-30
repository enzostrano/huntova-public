# Huntova v0.1.0a22 — 2026-04-30

Continuing the polish loop. Replaced two more hardcoded "Gemini"
references with provider-agnostic copy + fixed a CSS quirk on the
onboard banner.

## Updates

### Provider-agnostic copy
- Hunts idle screen step 2: "Gemini grades each candidate" → "Your
  AI provider grades each candidate". Same message, no hardcoded
  vendor.
- Wizard chat assist loading state: "Thinking with Gemini Pro..."
  → "Thinking…". Cleaner + works for any provider.

### Onboard banner CSS hygiene
- Removed duplicate `display:none` in inline style (banner had
  `display:none; … ; display:none; align-items:center`). JS still
  toggles to `display:flex` so it works either way, but the markup
  is no longer self-contradictory.

## Bug fixes
- None — all hygiene + copy.

## Known issues
- Pricing modal in cloud mode still names "Gemini Pro AI" as the
  Agency-tier upgrade benefit. Cosmetic only; cloud-mode users see
  this and it's the truth there.
- `CLAUDE.md` rewrite still TODO.
