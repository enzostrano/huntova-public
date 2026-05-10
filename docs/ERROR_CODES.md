# Huntova Error Codes

User-facing failures surface with a stable `HV-Exxxx` code so support, logs, and the chat dispatcher can give consistent guidance. Codes are append-only — never renumber. Don't add a code unless it has a fix the user can act on **and** is actually emitted from somewhere in the codebase. Phantom codes (documented but never emitted) are worse than no codes — they erode trust in the table.

## Live codes (currently emitted)

| Code | Surface | Means | Fix | Emitted from |
|------|---------|-------|-----|--------------|
| **HV-E1001** | Hunt | SearXNG unreachable | Install SearXNG locally (`huntova doctor`) or set `SEARXNG_URL` to a working public instance. Default `https://searx.be` is rate-limited. | `app.py` agent main loop |
| **HV-E1002** | Hunt | AI provider returned no completion | Check Settings → API keys. Anthropic / OpenAI / OpenRouter key may be expired, hit a rate-limit, or run out of credit. | `app.py` agent main loop |
| **HV-E1004** | Hunt | Brain wizard not completed | Open the Brain panel and finish all 9 steps. The agent needs ICP context to score prospects. | `app.py` agent main loop |

## How to surface a new code

1. Add the row above with **the file the code is emitted from** in the rightmost column. Don't add a row first and wire the emit later — the documentation drift gets out of hand fast.
2. Use the code in the corresponding `emit_status` / chat reply / API error so logs match docs.
3. Wire frontend `last_status` parsing to recognize the code if you want a tailored hint.

## Convention

- `HV-E1xxx` — Hunt lifecycle (1001 SearXNG, 1002 AI, 1004 Brain wizard)
- `HV-E2xxx` — Chat / provider routing (reserved range)
- `HV-E3xxx` — Brain wizard (reserved range)
- `HV-E4xxx` — Settings persistence (reserved range)
- `HV-E5xxx` — CRM / Leads (reserved range)
- `HV-E6xxx` — SSE / streaming (reserved range)
- `HV-E7xxx` — Local install / environment (reserved range)

The reserved ranges document where new codes will land when introduced. Do **not** populate them with phantom rows ahead of the emit code — every row in the live table above must be cited from at least one real `emit_status` / `emit_log` / API-response location.

## History

This file used to list 28 codes. An audit found 25 of them were never emitted anywhere — the doc had drifted into aspirational rather than factual territory. v0.1.0a263 stripped the phantoms and re-grounded the table on the three codes actually emitted. If a new code is needed, add the emit first, then document it.
