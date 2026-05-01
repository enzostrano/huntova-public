# Huntova Error Codes

Every user-facing failure surfaces with a stable `HV-Exxxx` code so support, logs, and the chat dispatcher can give consistent guidance. Codes are append-only — never renumber. Don't add a code unless it has a fix the user can act on.

| Code | Surface | Means | Fix |
|------|---------|-------|-----|
| **HV-E1001** | Hunt | SearXNG unreachable | Install SearXNG locally (`huntova doctor`) or set `SEARXNG_URL` to a working public instance. Default `https://searx.be` is rate-limited. |
| **HV-E1002** | Hunt | AI provider returned no completion | Check Settings → API keys. Anthropic / OpenAI / OpenRouter key may be expired, hit a rate-limit, or run out of credit. |
| **HV-E1003** | Hunt | No qualified leads after full sweep | Loosen your ICP in the Brain wizard — fewer required regions, broader buyer roles, or shorter exclusions list. |
| **HV-E1004** | Hunt | Brain wizard not completed | Open the Brain panel and finish all 9 steps. The agent needs ICP context to score prospects. |
| **HV-E1005** | Hunt | Concurrent hunt already running | Stop the active hunt from the Agent panel before starting another. Local mode allows one hunt at a time. |
| **HV-E1006** | Hunt | Cancel requested mid-run | The agent finished the in-flight URL and stopped. Restart anytime. |
| **HV-E1007** | Hunt | Credits exhausted (cloud only) | Local mode has no credits. If you see this in cloud, top up at the billing page. |
| **HV-E2001** | Chat | Provider returned 401 / 403 | API key is missing, malformed, or revoked. Re-enter in Settings → API keys. |
| **HV-E2002** | Chat | Provider returned 429 (rate-limit) | Wait 30–60s. If it persists, your provider account is hitting its tier cap. |
| **HV-E2003** | Chat | Provider returned 5xx | Provider outage. Switch to a different provider in Settings → Preferences and retry. |
| **HV-E2004** | Chat | No provider configured | Open Settings → API keys and add at least one provider key. |
| **HV-E2005** | Chat | Model name rejected by provider | Clear `preferred_model` in Settings — let the provider pick its default. |
| **HV-E3001** | Brain wizard | `/api/wizard/complete` rejected with vague_issues | Make answers more specific (15+ chars), pick at least 3 buyer roles + 2 regions. |
| **HV-E3002** | Brain wizard | Website scan timed out | Skip the URL field — paste your description manually instead. The scan is optional. |
| **HV-E3003** | Brain wizard | Assist returned generic fallback | Your provider key may be invalid. The wizard works without assist; finish the form anyway. |
| **HV-E4001** | Settings | CSRF validation failed | Refresh the page. Your session cookie may have expired. |
| **HV-E4002** | Settings | Save returned 4xx with key list | An unknown setting key was sent. Reload — your local build may be older than the server. |
| **HV-E4003** | Settings | Provider key save returned ok but didn't persist | Your OS keychain may be locked. macOS: re-open Keychain Access and unlock login. Linux: install `secretstorage`. |
| **HV-E5001** | CRM | Lead delete returned 404 | Lead was already deleted from another tab. Refresh the Leads panel. |
| **HV-E5002** | CRM | Email rewrite returned tone-mismatch | Use one of: friendly, consultative, broadcast, warm, formal. |
| **HV-E5003** | CRM | Research timed out | Provider was slow. Retry — research uses ~3 page fetches + one AI call. |
| **HV-E6001** | SSE | `/agent/events` connection dropped | Frontend reconnects automatically with exponential backoff. If it keeps dropping, check for a reverse-proxy that buffers responses. |
| **HV-E6002** | SSE | Server can't open SSE stream | Per-user event bus may have been GC'd. Reload the page. |
| **HV-E7001** | Local install | `huntova doctor --email` says SPF/DKIM/DMARC missing | Configure DNS on your sending domain before running cold-email sequences. |
| **HV-E7002** | Local install | Keychain unavailable | Falls back to plain-JSON config. Secrets are still saved but not OS-encrypted. |
| **HV-E7003** | Local install | Playwright browser missing | `huntova doctor` prints the install command. Optional — agent works without browser, just no deep-qualify pass. |

## How to surface a new code

1. Add the row above (alphabetic by code, never renumber).
2. Use the code in the corresponding `emit_status` / chat reply / API error so logs match docs.
3. Wire frontend `last_status` parsing to recognize the code if you want a tailored hint.

## Convention

- `HV-E1xxx` — Hunt lifecycle
- `HV-E2xxx` — Chat / provider routing
- `HV-E3xxx` — Brain wizard
- `HV-E4xxx` — Settings persistence
- `HV-E5xxx` — CRM / Leads
- `HV-E6xxx` — SSE / streaming
- `HV-E7xxx` — Local install / environment
