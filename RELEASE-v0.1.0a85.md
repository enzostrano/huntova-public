# Huntova v0.1.0a85 — 2026-05-01

## Bug fixes

### `dedup-by-domain` plugin lowercases the host
- `_domain_of` extracted host directly from the URL string and only
  stripped a literal `www.` prefix. Mixed-case hosts (`Example.com`
  vs `example.com`) and `WWW.example.com` weren't recognised as the
  same domain — the plugin let duplicate results through and wrote
  multiple entries to `seen_domains.jsonl`.
- Now lowercases the host before the `www.` strip.

### `huntova rm` exits non-zero when the user aborts
- Typing `n` (or anything other than `y`/`yes`) at the confirmation
  prompt printed `aborted.` but returned exit code **0** — scripts
  treating `huntova rm` as a guard couldn't distinguish "deleted"
  from "user said no". The Ctrl-C path already returned 130; the
  declined-prompt path now returns 1.

### SearXNG / fallback search session sets a real User-Agent
- The shared `_search_session` was created without a `User-Agent`
  header, so requests inherited Python's default `python-requests/...`
  string. Several public SearXNG instances and DDG return 403 / rate
  limit immediately for that UA, so SearXNG calls failed silently and
  fell back to DDG more often than they should have.
- Now the session sets a Huntova-branded UA + a JSON-first `Accept`
  header at construction time. Per-request override calls (DDG, Jina)
  still pass their own headers.

### CSV export ships a UTF-8 BOM (Excel mojibake fix)
- `/api/export/csv` returned UTF-8 bytes without a BOM. Excel for
  Windows guesses Windows-1252 on a no-BOM CSV, so accented org
  names (`Café Müller`, `São Paulo`, `Münchner ...`) showed as
  `CafÃ© MÃ¼ller`. macOS Numbers + Google Sheets handled it fine,
  but Excel users got garbage for any non-ASCII character.
- Now prepends `﻿` and sets `media_type="text/csv; charset=utf-8"`.

### `crawl_prospect` skips PDF / binary responses by Content-Type
- The fetch path checked the URL extension for `.pdf` upstream, but
  servers that serve PDFs from extension-less URLs (e.g. CMS routes)
  slipped through. The bytes were then run through HTML regex
  (`<script>`, `<style>`, `<[^>]+>`) which produced binary garbage
  that polluted the page-text passed to the AI scorer.
- Now reads `Content-Type` first and bails with `("", "")` for PDF,
  image/*, audio/*, video/*, octet-stream, or zip responses before
  the regex stage.

### `_clean_subject` strips `Re:` / `Fwd:` prefixes
- AI providers occasionally invent a subject prefixed with `Re:` or
  `Fwd:` even though the email is a fresh cold outreach. That
  destroys credibility — the recipient sees a "reply" to a thread
  they don't remember.
- `_clean_subject` already stripped a `Subject:` prefix; now also
  strips `Re:`, `Fw:`, `Fwd:` (case-insensitive).

## Updates
- None.

## Known issues
- Same as a84.
