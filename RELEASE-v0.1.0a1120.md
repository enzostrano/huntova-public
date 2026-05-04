# Huntova v0.1.0a1120

Wave-3 swarm bug-hunt: public-share `/h/<slug>` route audit.

## Bug fixes

- **Capability gate enforced** (`server.py:2678`, `2720`, `2740`,
  `2958`, `2974`, `2936`, `3018`, `1362`). The
  `public_share_enabled` capability declared in `runtime.py:35` was
  never checked at the route layer, so admins setting
  `HV_PUBLIC_SHARE=0` still saw every share endpoint serving content.
  Six routes now 404 when public sharing is disabled: `/h/<slug>`,
  `/h/<slug>.json`, `/h/<slug>/og.svg`, `/api/share/<slug>/views`,
  `POST /api/hunts/share`, `POST /api/try`. Revocation
  (`POST /api/hunts/share/<slug>/revoke`) stays reachable so admins
  who flip the flag mid-flight can still revoke pre-existing links.

- **Slug collision retry on `create_hunt_share`** (`db.py:2045`).
  `secrets.token_urlsafe(8)` collisions are astronomically unlikely
  (~64 bits of entropy) but theoretically possible — and the prior
  code would surface a SQLite `IntegrityError` (PRIMARY KEY violation)
  to the user as an unhandled 500. The mint loop now retries up to
  five times before giving up. The `publish_public_recipe` retry
  loop in `db.py:2215` was already correct; this brings shares to
  parity.

- **OG image + JSON share endpoints no longer inflate view_count**
  (`db.py:2069` `get_hunt_share` gained a `bump_views: bool = False`
  flag; `server.py:2974` HTML route opts in, OG and JSON routes
  default-out). Previously every Slackbot/Twitterbot/Discordbot
  unfurl that hit `/h/<slug>/og.svg` and every `huntova hunt
  --from-share` CLI fork that pulled `/h/<slug>.json` silently
  bumped the share owner's `view_count`. Only the actual HTML page
  render now counts as a view.

- **`X-Robots-Tag: noindex, nofollow` on `/h/<slug>.json`**
  (`server.py:2958`). Defence-in-depth: the HTML route already had
  a `<meta name="robots" content="noindex,nofollow">` tag, but the
  JSON sibling had no equivalent header. Any crawler that scrapes
  the API surface via Referer leak or sitemap injection is now
  explicitly told to stay out.

- **Slug shape validation tightened on OG route** (`server.py:2936`).
  The HTML and JSON routes already enforced
  `[A-Za-z0-9_-]{4,32}`; the OG svg route passed the slug straight
  to the DB. Same fence is now applied so a malformed slug 404s
  before touching SQLite.

## What was checked but is fine

- **Slug entropy.** `secrets.token_urlsafe(8)` = 64 bits of CSPRNG
  output → ~11 base64url chars. Not enumerable.
- **PII leakage.** `_SHARE_LEAD_FIELDS` (`server.py:2501`) is an
  allowlist of public-safe fields. `contact_email`, `contact_phone`,
  `contact_name`, internal scoring metadata, wizard data, and ICP
  description are all excluded. Pinned with a regression test.
- **`user_id` on JSON.** `/h/<slug>.json` already strips `user_id`
  before publishing (`server.py:2970`). Pinned with a test.
- **`<meta name="robots" content="noindex,nofollow">`** on the HTML
  shell (`server.py:3513`). Already present; pinned with a test.
- **Slug case-sensitivity.** SQLite TEXT and Postgres TEXT both use
  case-sensitive equality by default. The slug regex allows mixed
  case and `secrets.token_urlsafe` produces mixed case, so behaviour
  is consistent: `/h/AbcXyz` and `/h/abcxyz` are different shares.
  Acceptable — minting is random so collisions only happen if a user
  manually constructs a URL with the wrong case, which 404s cleanly.
- **Revocation latency.** OG svg has `Cache-Control: public,
  max-age=3600`. After revocation a CDN may serve a stale OG image
  for up to an hour, but the HTML page itself revokes immediately
  (no caching). Acceptable — the OG image alone leaks no extra data.

## Tests

- `tests/test_public_share.py` — 14 new tests covering the four
  bug-classes plus PII / user_id / robots / slug-shape regression
  guards.

## Files

- `server.py` — capability gate on six share routes; bump-views
  flag plumbed through HTML/JSON/OG handlers; X-Robots-Tag header on
  JSON; slug-shape validation on OG route.
- `db.py` — collision-retry loop on `create_hunt_share`;
  `bump_views` flag on `get_hunt_share`.
- `tests/test_public_share.py` — new file (14 tests).
- `cli.py` — VERSION bump.
- `pyproject.toml` — version bump.
