# Huntova v0.1.0a46 — 2026-05-01

Five bugs surfaced by parallel Explore-agent audits, batched. Each
agent scoped to a different module; concrete file:line findings
applied as minimal fixes.

## Bug fixes

### `providers.py:344-360` — custom provider base_url normalize
- User sets `HV_CUSTOM_BASE_URL=https://api.example.com` (no `/v1`)
  → `OpenAI(base_url=...)` 404s on every chat call.
- Now: strip trailing slash, append `/v1` unless the URL already
  ends in `/v1`, contains `/v1/`, ends in `/api`, or ends in
  `/openai`.

### `agent_runner.py:442-454` — `thread.start()` try/except in queue path
- `start_agent()` already wrapped its `thread.start()` in
  `try/except` to clean up `self._running` on failure.
- `_process_queue()` did not — a failed start would leave a stale
  Thread object in the dict that `is_alive()` returns False for,
  blocking the user's next attempt.
- Mirrored the existing pattern: pop from `_running`, set
  `agent_running=False`, re-raise.

### `cli.py:_emit_json` — UTF-8 stdout encoding
- `huntova hunt --json` would crash on Windows cmd.exe (or any
  stdout that isn't UTF-8) when leads contain accented chars
  ("Müller", "François") or emojis.
- Now uses `sys.stdout.buffer.write(...)` with explicit UTF-8
  bytes + `ensure_ascii=False` for human-readable output. Falls
  back to text-mode write on stdout types without `.buffer`
  (rare — pytest capture, weird CI runners).

### `auth.py:set_csrf_cookie` — CSRF cookie lifetime
- Was: hardcoded `max_age=72 * 3600`.
- Now: `SESSION_EXPIRY_HOURS * 3600` so the CSRF cookie expires
  on the same schedule as the session cookie. If `SESSION_EXPIRY_HOURS`
  gets tightened (e.g. 24h), the CSRF cookie no longer outlives
  the session — closes a small replay-window surface.

### `db.py:_admin_apply_credit_change_sync` — Postgres-only syntax
- Hardcoded `cursor_factory=psycopg2.extras.RealDictCursor` +
  `%s::int` cast in the `set_exact` branch. Latent landmine if
  this admin function ever runs in SQLite mode.
- Switched to `_cursor(conn)` driver-agnostic wrapper; dropped
  the `::int` cast (`amount` is already an int). Cloud behavior
  unchanged.

## Updates
- None.

## Known issues
- Same as a45.

## Process note
- 12-agent parallel hunt was launched; 4 returned actionable bugs
  in this batch. Remaining agents still working — next release
  will batch their findings.
