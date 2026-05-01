# Huntova v0.1.0a53 — 2026-05-01

Three more agent-found bugs.

## Bug fixes

### `static/install.sh` — `pipx upgrade --force`
- Was: `pipx upgrade huntova` without `--force`. If the local
  venv was broken (missing deps, half-installed), upgrade silently
  no-ops and the user re-runs the installer to no effect.
- Now: `pipx upgrade --force huntova` always nukes + rebuilds the
  venv. Idempotent for broken prior installs. Falls back to
  `pipx install --force` if upgrade fails.

### `cli.py:cmd_history` — try/finally on DB connection
- `cur.execute()` or `cur.fetchall()` raising left the Postgres
  pool slot stranded (cloud mode). SQLite's singleton conn means
  no leak there, but the symmetric try/finally keeps the pattern
  consistent across drivers.

### `db.py:get_lead_feedback_recent` — ORDER BY tiebreaker
- Two feedback rows sharing a `created_at` (bulk teach imports,
  rapid Good Fit clicks) returned in driver-defined order — unstable
  across calls and pagination. Same fix `get_leads()` (#28) already
  has: add `lf.id DESC` as the secondary sort key.

## Updates
- None.

## Known issues
- Same as a52.
