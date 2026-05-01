# Huntova v0.1.0a55 — 2026-05-01

## Bug fixes

### `huntova recipe import-url` URL parse fix
- Was: `json_url = url if url.endswith(".json") else (url.rstrip("/") + ".json")`.
  When the user passed a URL with a query string (e.g.
  `https://example.com/r/myrecipe?ref=team`), the rstrip + append
  produced `https://example.com/r/myrecipe?ref=team.json` — invalid
  because `.json` ended up inside the query value, not on the path.
  Server returned 404.
- Now: parses the URL via `urllib.parse.urlparse`, appends `.json`
  to `path` only, then re-assembles with `urlunparse`. Query string
  + fragment preserved untouched.

## Updates
- None.

## Known issues
- Same as a54.
