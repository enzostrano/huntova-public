# Huntova v0.1.0a87 — 2026-05-01

## Bug fixes

### LinkedIn URL extraction handles country-code subdomains
- The regex only matched `linkedin.com` and `www.linkedin.com`, so
  international company pages on `uk.linkedin.com`, `de.linkedin.com`,
  `fr.linkedin.com`, etc. were silently dropped from contact
  enrichment. The same prospect's contact page on `uk.linkedin.com/in/...`
  never made it into the lead.
- Pattern now accepts `(?:[a-z]{2}\.|www\.)?linkedin\.com`.

### Leadership-role regex word-boundaries
- The team-page leader picker used `re.search("CEO|founder|...")`
  with no boundaries, so a phrase like "CEOgrade" or "directorate"
  wrongly matched the role list and the wrong person was picked as
  the lead's contact.
- Now uses `\b(?:CEO|founder|director|...)\b`.

### `is_recurring` keyword tuple includes annual / yearly / weekly
- The recurring-event detector recognised `recurr`, `ongoing`,
  `monthly`, `quarter` — but not `annual`, `yearly`, `weekly`, or
  the explicit `every year` / `every month` / `every week`. So an
  "Annual Conference" or a "Yearly Summit" was tagged as
  one-shot — even though those are the highest-value recurring
  pipelines.
- Added the missing keywords.

### SMTP port 465 uses implicit TLS (`SMTP_SSL`)
- `_send_email_sync` always called `smtplib.SMTP(...)` then
  `starttls(...)`. That handshake fails on port 465 because port 465
  speaks implicit TLS — the connection is encrypted from byte zero,
  there's nothing to STARTTLS-upgrade. Cloud users with a 465
  provider (most webmail relays) couldn't send transactional email.
- Now branches: port 465 → `SMTP_SSL` with the same default-context
  (cert + hostname verification preserved). Other ports keep the
  STARTTLS path.

## Updates
- None.

## Known issues
- Same as a86.
