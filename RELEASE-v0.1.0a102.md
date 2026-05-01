# Huntova v0.1.0a102 — 2026-05-01

## Bug fixes

### `/h/<slug>` share pages set private no-cache + `X-Robots-Tag`
- The public share-page handler returned an `HTMLResponse` without
  any cache-control or robots metadata. The HTML already includes a
  `<meta name="robots" content="noindex,nofollow">` tag, but
  search-bot crawlers and intermediate CDNs ignore HTML meta hints
  for non-HTML purposes (caching, redirect-from-search), so a
  shared lead page could end up cached at the edge or briefly
  surfaced via a misbehaving crawler.
- Added explicit response headers:
  - `Cache-Control: private, no-cache, no-store, must-revalidate` —
    individual users still get fresh data on every load, and the CDN
    layer must not store the response on behalf of multiple
    visitors.
  - `X-Robots-Tag: noindex, nofollow` — defence-in-depth on top of
    the existing meta tag for crawlers that prefer the header.

## Updates
- None.

## Known issues
- Same as a101.
