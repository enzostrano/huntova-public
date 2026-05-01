# Huntova v0.1.0a51 — 2026-05-01

Three more agent-found bugs batched.

## Bug fixes

### `app.py` competitor_signals None-safety
- `services` (from wizard_data) and `competitor_signals` (from
  AI-generated strategy JSON) were iterated and `.lower().split()`'d
  without null/type guards. A None or non-string in either list
  → `AttributeError: 'NoneType' object has no attribute 'lower'`
  → agent crash mid-strategy-build.
- Now: `for s in (services or [])` + `(str(s) if s else "")` for
  each entry. Same defense for `anti.get("competitor_signals")`.

### `app.py:crawl_prospect._fetch` streams + caps body
- Was: `requests.get(..., timeout=10, verify=False)` without
  `stream=True` would buffer the entire response into memory before
  the size check at `len(r.content) <= 2_000_000`. A 50MB PDF or
  malicious large response would OOM the agent thread.
- Now: streams with `iter_content(chunk_size=64KB)`, pre-checks
  `Content-Length` header, hard-caps at 2MB during accumulation.
  Mirrors the streaming pattern `fetch_url()` already uses (bug #67).
- Same fix applied to the Jina Reader fallback path.

### `db.py` merge_lead + merge_settings now SQLite-safe
- Both atomic helpers used `cur.execute("... FOR UPDATE", ...)`
  directly without going through `_xlate()`. SQLite doesn't
  support `FOR UPDATE` syntax → would crash if either helper ran
  in local mode.
- Wrapped both queries in `_xlate(...)` so the driver translator
  strips the unsupported `FOR UPDATE` clause for SQLite. Cloud
  Postgres path unchanged.
- Bonus: switched both from hardcoded `psycopg2.extras.RealDictCursor`
  to `_cursor(conn)` driver-agnostic wrapper.

## Updates
- None.

## Known issues
- Same as a50.
