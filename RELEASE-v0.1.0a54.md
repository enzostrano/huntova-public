# Huntova v0.1.0a54 — 2026-05-01

Two more agent-found bugs.

## Bug fixes

### `/api/status` — explicit no-cache headers
- The endpoint returned a plain dict via FastAPI's default JSON
  serializer, no Cache-Control header. A reverse proxy or browser
  cache could hold a stale `running` response for minutes after
  the agent finished, masking the real idle/stopping state to the
  dashboard's 5s polling loop.
- Now: returns `JSONResponse(result, headers={"Cache-Control":
  "no-cache, no-store, must-revalidate", "Pragma": "no-cache"})`.
  Mirrors the no-cache header already on `/agent/events` (SSE).

### `app.py` JSON-LD `@graph` envelope unpacked
- Many sites wrap their JSON-LD in `{"@graph": [...]}` (Yoast,
  WordPress, e-commerce schema generators all emit this shape).
  The parser only handled top-level arrays + top-level dicts with
  recognized `@type` — `@graph` envelopes had no `@type` so the
  whole block was silently skipped.
- Now: detects `@graph` arrays and unpacks the contained entities
  (Organization, Event, ContactPoint, etc.) for extraction. Matches
  the existing top-level-array branch.

## Updates
- None.

## Known issues
- Same as a53.
