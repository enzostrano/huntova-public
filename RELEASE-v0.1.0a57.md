# Huntova v0.1.0a57 — 2026-05-01

Three more agent-found bugs.

## Bug fixes

### `cli.py:cmd_outreach` — catch ValueError on template format
- `_render(template, lead)` only caught `KeyError` and `IndexError`
  from `template.format(**ctx)`. A template containing a literal
  `{` / `}` (or unmatched braces) raises `ValueError` which
  propagated and crashed the whole outreach send loop.
- Now: `except (KeyError, IndexError, ValueError)` — falls back to
  the raw template so the loop continues.

### `db_driver.py:_pg_to_sqlite` — translate GREATEST/LEAST → MAX/MIN
- Several db.py paths use `GREATEST(0, credits_remaining - %s)` for
  clamped subtraction. SQLite doesn't expose `GREATEST` (only the
  aggregate `MAX`). The translator missed it → `no such function:
  GREATEST` on every credit-revoke in local mode.
- Now: regex translates `GREATEST(` → `MAX(` and `LEAST(` → `MIN(`.
  SQLite's MAX/MIN scalar functions accept ≥2 args and behave the
  same as Postgres GREATEST/LEAST.

### `static/app.js:openLeadModal` — re-entry guard
- Rapid double-clicks on a CRM row called `openLeadModal(lid)`
  twice synchronously, briefly thrashing the modal DOM as both
  renders raced.
- Now: bail early if `#leadModalBg` already has `.on` class.
  Second click is a no-op.

## Updates
- None.

## Known issues
- Same as a56.
