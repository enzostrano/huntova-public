# Huntova v0.1.0a58 — 2026-05-01

Two more agent-found bugs.

## Bug fixes

### `static/app.js:huntNarrate` — case-insensitive dedup
- The throttle dedup at `huntNarrate` compared keys via `===`. Two
  SSE events arriving with `search:Foo` and `search:foo` within the
  350ms window would both render — visible flicker on the hunt
  panel.
- Now: lowercases the key before comparison + storage.

### `app.py` query-dedup strips ALL quotes
- Was: `key = q.lower().strip('"')` only stripped quotes from the
  string ends. Two AI-generated queries that differed only in
  internal quoting — `"pharma sales" Germany` vs
  `pharma sales germany` — both passed through, burning two query
  slots on the same intent.
- Now: removes every `"` and collapses whitespace before
  building the dedup key. True semantic match.

## Updates
- None.

## Known issues
- Same as a57.
