# Huntova v0.1.0a88 — 2026-05-01

## Bug fixes

### `/api/update` caps `notes` at 4 000 characters
- The lead-update endpoint took whatever the client sent for `notes`
  and wrote it to the DB unbounded. The admin-token endpoint already
  truncates to 400 chars, but the user-facing path didn't, so a slip
  could push a multi-MB string into the row.
- Now coerces `None`/missing to `""` and slices to 4 000 — generous
  enough for legitimate notes, hard ceiling against accidental
  payload bombs.

### Dead `_lp_instr` lookup removed from `analyse_lead`
- The function had a line `_lp_instr = _w.get("_learning_instructions", "") if '_w' in dir() else ""`. `_w` is never defined in
  this scope, so the conditional was always false and `_lp_instr`
  was always empty — and never read after the assignment anyway.
  The real value already comes from `_wiz_lp = load_settings().get("wizard", {}).get("_learning_instructions", "")` two lines down.
- Dead code deleted; behaviour unchanged.

## Updates
- None.

## Known issues
- Same as a87.
