# Privacy

Huntova is a local-first BYOK CLI. The default install — `pipx install huntova` followed by `huntova onboard` — runs entirely on your machine and never transmits data to a Huntova-controlled server.

This document describes (a) what data the local CLI handles, (b) what additional handling applies when you opt in to a Huntova-hosted surface (the marketing site `huntova.com`, the `/try` demo, public share pages, the cloud SaaS), and (c) the third parties involved when you exercise either path.

---

## Local CLI mode (default)

When you run Huntova locally:

- **Your AI provider key** is stored in your OS keychain (macOS Keychain / Windows Credential Manager / libsecret on Linux). If no keychain backend is available, it falls back to a Fernet-encrypted file at `~/.config/huntova/secrets.enc`. Last resort is a 0600-locked plaintext JSON at `~/.config/huntova/secrets.json` with a startup warning. **Huntova has no proxy, middle-tier, or telemetry that ever sees this key** — the CLI sends it directly from your machine to your chosen provider's API.
- **Your leads, hunt history, settings, and learning data** live in `~/.local/share/huntova/db.sqlite`. They never leave your disk unless you explicitly export them or share them.
- **Backups** of your leads JSON live at `~/.local/share/huntova/backups/` (tiered rotation: 10 hourly, 7 daily, 4 weekly, 12 monthly).
- **Session logs** (one per hunt) live at `~/.local/share/huntova/logs/`.
- **No analytics, telemetry, or crash reports are sent**. The local CLI has zero outbound network calls outside (a) your AI provider, (b) your configured SearXNG instance, (c) the URLs the agent fetches during a hunt, and (d) optional Playwright/Jina fallbacks for JS-heavy pages.

### Third parties touched by the local CLI

Each call only happens because *you* configured it:

| Third party | When it's contacted | What's sent |
|-------------|---------------------|-------------|
| Your AI provider (Anthropic / OpenAI / Gemini / etc.) | Every scoring, drafting, and chat call | Prompt text + your API key |
| SearXNG (your configured instance) | Each search query | Query terms only |
| Public web hosts | URL fetch during qualify | Standard HTTP `GET` with our User-Agent |
| `r.jina.ai` (Jina Reader) | Optional fallback for JS-heavy pages | The target URL only |
| Your SMTP relay | When you opt in to outbound email | Your drafted message + recipient address |
| Your Slack webhook (if configured) | Optional plugin emit | Lead name + URL |

You can disable each opt-in surface independently via env vars (see `huntova doctor` for the live state).

### What stays on your machine, period

- AI provider key
- Lead database
- Search query history
- Drafted emails (until you actively send)
- Lead-feedback signals + the per-user instruction summary the agent learns from
- Session logs and backups

### Data export and deletion

- `huntova export --format json > leads.json` — ship the entire lead set out as JSON or CSV.
- Delete `~/.local/share/huntova/db.sqlite` to forget every lead, hunt, and feedback signal in one move.
- Delete `~/.config/huntova/` to forget every stored API key and config.
- `pipx uninstall huntova` removes the CLI itself.

---

## Cloud SaaS mode

A hosted version of Huntova exists at `huntova.com` for users who don't want to run their own SearXNG or store their own data. It is the same codebase with `APP_MODE=cloud`. If you sign in to the hosted version:

- We process your email address, hashed password (bcrypt), and an optional display name to maintain your account.
- Your AI provider key, if you choose to store it, is encrypted at rest in PostgreSQL and used only to make calls on your behalf.
- Your leads, drafts, and hunt history are stored in PostgreSQL alongside your user record. We never sell them, never share them with third-party "data brokers," and never use them to train a model.
- We log billing events (Stripe webhooks), authentication events, and admin actions. These are retained as long as your account is active and for up to 12 months after deletion to satisfy financial-record obligations.
- We do not run third-party analytics scripts on the dashboard surface. The marketing site (`huntova.com/`) uses a single first-party event endpoint to count anonymous page views.

### Third parties used in cloud mode

| Vendor | Role | Data shared |
|--------|------|-------------|
| Stripe | Payments | Email, billing address, last-4, payment intent IDs |
| Postmark / SendGrid (your choice via SMTP) | Transactional email | Your email + the email body Huntova sent |
| Anthropic / OpenAI / Gemini | The model behind your hunt | Prompt + the key you supplied |
| Railway / your chosen host | Infrastructure | Standard hosting telemetry |
| GitHub | Source distribution | Public repo only — no user data |

### Account deletion

Cloud users can request full deletion via `Account → Delete account` (or by emailing `enzostrano@gmail.com`). Within 30 days we erase: account record, leads, hunt history, drafts, learning data, audit log lines tied to your user_id. Anonymized aggregate counters (total signups, total hunts) survive.

---

## Public share pages (`/h/<slug>`)

When you mint a public share page via `huntova share`, the lead snapshot is stored either in your local SQLite (local mode) or in the cloud database (cloud mode). The page is served with `Cache-Control: private, no-cache, no-store, must-revalidate` and `X-Robots-Tag: noindex, nofollow` — search engines don't index it and CDN intermediaries are instructed not to cache. Visitors who load the page hit a per-slug view counter. You can revoke a share at any time via `huntova share revoke <slug>`.

---

## `/try` demo

`huntova.com/try` runs a 3-lead Proof Pack against a synthetic dataset (clearly labelled "Preview Mode"). It does not consume credits, does not hit live web search, and does not store the ICP you typed. The IP-based rate limit is the only state it keeps for ~24 hours.

---

## Children

Huntova is not directed at children under 16 and we do not knowingly process data from anyone under 16.

---

## Changes to this policy

Material changes are noted in `CHANGELOG.md` and dated. We don't backdate.

## Contact

For privacy questions or to file a request: **enzostrano@gmail.com**.
