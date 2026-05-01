# Huntova v0.1.0a17 — 2026-04-30

The OpenClaw-shape release. Restructured the dashboard shell to
match the OpenClaw reference: 240px left sidebar with grouped
sections (CHAT / DASHBOARD / AGENT / SETTINGS), brand kicker, and a
chat slideover panel. Backend gained `/api/chat` for the slideover.

## Updates

### OpenClaw-style left sidebar
- New 240px `<aside class="hv-sidebar">` with grouped sections.
- Brand kicker top: HUNTOVA + "DASHBOARD". Version footer bottom.
- Topnav `.topnav-centre`, `.topnav-left .topnav-logo`, `.topnav-left
  .dot` hidden at viewport ≥900px (sidebar takes their role).

### Chat slideover
- New `<aside class="hv-chat-panel">` slides in from the right when
  the CHAT row is clicked. Mirrors OpenClaw's chat surface exactly:
  message log, input box, send button.
- Wired to `POST /api/chat` (server.py): same JSON-action shape as
  the `huntova chat` CLI. Anthropic JSON-mode prefill trick used to
  force structured output (Anthropic has no native `response_format`).
- DOM-safe: every message rendered with `textContent` + DOM nodes,
  no `innerHTML` (no XSS surface).

### `/api/chat` endpoint
- New POST route in `server.py`. Body: `{message: str}`. Response:
  parsed JSON action from the configured provider.
- Added to `CSRF_EXEMPT_PATHS` (local-mode tool, no session cookie).

### `/api/runtime` exposes version
- Endpoint now returns `version` alongside the runtime capabilities
  block. Frontend reads it to populate the sidebar version string at
  load time.

## Bug fixes
- None — additive release.

## Known issues
- Sidebar links to `/plugins`, `/demo`, `/setup` eject out of the
  dashboard shell (they're standalone pages). Lands a18 with
  `target="_blank"` + ↗ arrow.
- Mobile sidebar (<900px) falls back to topnav — not yet a proper
  drawer.
