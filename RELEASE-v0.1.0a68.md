# Huntova v0.1.0a68 — 2026-05-01

## Bug fixes

### `exportCSV` defends against formula injection
- A lead with `org_name="=cmd|'/c calc'!A0"` (or any cell starting
  with `=`, `+`, `-`, `@`) executes as a formula when a colleague
  opens the exported CSV in Excel / Google Sheets / Numbers.
  Real-world attack surface — a hostile lead could RCE on the
  user's coworker's machine.
- Now: prefix a single quote `'` to any cell whose first character
  is `=`, `+`, `-`, `@`, tab, or CR. Spreadsheet then treats the
  value as text. Standard OWASP CSV-injection defense.

## Updates
- None.

## Known issues
- Same as a67.
