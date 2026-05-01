# Huntova v0.1.0a72 — 2026-05-01

## Bug fixes

### `extract_linkedin_urls` regex anchor + cleanup
- The slug regex `[a-zA-Z0-9_-]+/?` matched
  `linkedin.com/company/acme/jobs` as `linkedin.com/company/acme`.
  Sub-paths (`jobs`, `recent-activity`, `posts`) silently became
  company-profile URLs in the CRM.
- Now: slug capture is anchored on a `/`, `?`, `#`, `"`, whitespace,
  or end-of-string boundary. Sub-paths are no longer matched. Plus
  we strip any trailing slash + drop query strings + fragments
  before storing — old `linkedin.com/in/john?utm_source=twitter`
  now stores as `linkedin.com/in/john`.

## Updates
- None.

## Known issues
- Same as a71.
