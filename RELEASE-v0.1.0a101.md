# Huntova v0.1.0a101 — 2026-05-01

## Bug fixes

### Page-text strip decodes HTML entities before the AI sees it
- After stripping `<script>`, `<style>`, and HTML tags, the residual
  text still contained literal entities like `&amp;`, `&quot;`,
  `&mdash;`, `&nbsp;`. The AI scoring prompt was therefore reading
  `Acme &amp; Co.` instead of `Acme & Co.` and `&quot;exit
  strategy&quot;` instead of `"exit strategy"`. That polluted the
  evidence-quote field, the `org_name` capture, and any string-match
  heuristic that ran on the cleaned text.
- Both `_strip_html()` (used by the search-result preview path) and
  the inline strip inside `crawl_prospect._fetch` now run
  `html.unescape(...)` after the tag-removal regexes. Whitespace
  normalisation still happens last so collapsed-NBSP doesn't leave
  double spaces.

### `upsert_lead` preserves user-set `email_status` and high `fit_score`
- `ON CONFLICT DO UPDATE SET email_status = %s` unconditionally reset
  the user's manual CRM state (`replied`, `meeting_booked`,
  `verified_contacted`, ...) back to whatever the agent emitted
  (typically `"new"`) every time the same `lead_id` was re-discovered
  in a later hunt. Same for `fit_score`: a re-encounter via a
  shorter / less-rich page could overwrite a previous high-quality
  score with a lower one.
- Switched to `EXCLUDED`-named columns and added two
  preservation rules:
  - `fit_score = GREATEST(leads.fit_score, EXCLUDED.fit_score)`
  - `email_status = CASE WHEN leads.email_status <> 'new' THEN
    leads.email_status ELSE EXCLUDED.email_status END`
- `data` (the JSON blob), `org_name`, and `country` still update,
  since those reflect the freshest extraction.

## Updates
- None.

## Known issues
- Same as a100.
