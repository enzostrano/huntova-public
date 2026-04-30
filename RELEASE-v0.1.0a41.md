# Huntova v0.1.0a41 — 2026-05-01

DRY refactor of the four no-provider preflights added in a37+a40.
Plus a fifth preflight on `/api/neo-chat` that was missed.

## Updates

### `_local_no_provider_response()` helper
- The four preflight blocks added across a37–a40 (in `/agent/control`,
  `/api/research`, `/api/agent-dna/generate`, `/api/rewrite`) were
  identical 11-line copy-paste blocks. Extracted into a single
  `_local_no_provider_response()` helper near the top of `server.py`.
- Each callsite now reads:
  ```py
  _np = _local_no_provider_response()
  if _np is not None: return _np
  ```
- Helper returns `None` in cloud mode or when at least one provider
  is configured, otherwise a 400 `JSONResponse` with the actionable
  "Open Settings → Providers" message.

### `/api/neo-chat` joins the preflight set
- Lead-detail Chat with Huntova about this email (Neo Chat panel)
  was hitting AI without preflight. Now uses the same helper so
  no-provider users get the same actionable error.

## Bug fixes
- None new.

## Verified
- Server boots, /api/runtime + /api/setup/status return clean.

## Known issues
- Same as a40.
