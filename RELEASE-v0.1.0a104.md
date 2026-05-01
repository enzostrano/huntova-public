# Huntova v0.1.0a104 — 2026-05-01

## Bug fixes

### `huntova migrate` exits non-zero when zero rows imported
- The migrate subcommand returned exit code 0 even when zero leads
  were imported. That happened in three benign-looking cases:
  - Header column-name case-mismatch (`"Email"` vs `"email"`),
    so all column lookups failed silently.
  - Every row already existed in the DB (all skipped as duplicate).
  - `--source` flag picked the wrong adapter for the file.
- A shell loop / scheduled job downstream then treated the migrate
  as success and moved on. Now the command emits
  `"[huntova] no valid rows imported"` to stderr and returns exit
  code `1`. Mixed-success runs (some imported + some errors) still
  return `2` as before.

## Updates
- None.

## Known issues
- Same as a103.
