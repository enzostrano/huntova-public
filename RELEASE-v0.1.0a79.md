# Huntova v0.1.0a79 — 2026-05-01

Four agent-found bugs.

## Bug fixes

### `app.py` SearXNG response shape defense
- `r.json()` returning a non-dict (list, string, null from a
  misconfigured proxy) hit `data.get("results")` with
  `AttributeError`. The exception bubbled up, caller silently fell
  back to DuckDuckGo without any signal that SearXNG was broken.
- Now: `if not isinstance(data, dict): data = {}` guard before the
  `.get` call.

### `account.html:changePassword` disables button during fetch
- Was: panicky double-click could fire two concurrent password-
  change POSTs, racing on bcrypt hash. `saveProfile()` already
  has the disable-during-fetch pattern.
- Now: button disabled at fetch start, re-enabled in `.finally()`.

### `server.py` email-verify uses constant-time compare
- The verify-email handler compared the token's email to the user's
  email via `!=`. Character-by-character short-circuit leaks
  matching prefix length via response time — a timing oracle for
  enumerating valid addresses.
- Now: `secrets.compare_digest()` for constant-time match.

### `app.py` content-freshness year regex covers 2030+
- `\b(20[12]\d)\b` only matched 2010–2029. Past 2029 a fresh
  "2031" copyright was invisible to the freshness check —
  prospects with current content silently lost the green-flag.
- Widened to `\b(20\d{2})\b` (any 2000–2099). Same fix on the
  copyright-year regex.

## Updates
- None.

## Known issues
- Same as a78.
