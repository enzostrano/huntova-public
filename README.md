# Huntova

**Your AI sales team. Local-first. Open source.**

Huntova is an open-source agent fleet for outbound. The **Hunter** finds
prospects on the live web. The **Researcher** crawls each site for
hooks. The **Composer** drafts personalised email. The **Sequencer**
schedules follow-ups. The **Inbox-watcher** classifies replies. The
**Pre-flight** audits your sending domain. The **Coach** surfaces what's
working. Every agent runs locally, on your machine, against *your* AI
key.

> Open-source alternative to commercial lead-generation, sales-research,
> and outbound-email tooling. No subscription. No credits. Your data
> stays on your disk. Your AI key never leaves your machine.
>
> *Mentioned third-party tools are trademarks of their respective
> owners; references are comparative and factual. Huntova is not
> affiliated with, endorsed by, or partnered with any of them.*

```bash
pipx install huntova
huntova onboard                       # 60-second TUI wizard, OS keychain
huntova hunt --max-leads 5            # short demo run; full hunts take longer
```

Prefer the web wizard? Run `huntova onboard --browser` to skip the TUI
and configure providers in your browser instead.

Or via curl:

```bash
# Once huntova.com is deployed:
curl -fsSL https://huntova.com/install.sh | sh

# Or directly from the GitHub raw URL (works today):
curl -fsSL https://raw.githubusercontent.com/enzostrano/huntova-public/main/static/install.sh | sh
```

Or by cloning the public repo:

```bash
git clone https://github.com/enzostrano/huntova-public.git
cd huntova-public

# Use Python 3.11+ explicitly (macOS default is 3.9, too old)
python3.13 -m venv .venv      # or python3.11 / python3.12
source .venv/bin/activate

# Upgrade pip — pyproject.toml-only installs need pip 21.3+
pip install --upgrade pip

pip install -e .
huntova onboard
```

> **Why the explicit Python and pip upgrade?** Huntova uses modern
> Python type syntax (`dict | None`, requires 3.11+) and ships as a
> `pyproject.toml`-only project. macOS's bundled Python 3.9 + pip
> 21.2 will fail with `setup.py not found`. Upgrading both is a 5-
> second fix.

## What it does

1. **Sweep** — SearXNG-powered web search across Google, Bing, DuckDuckGo. 60–80 AI-generated queries per hunt, filtered for noise.
2. **Score** — every prospect graded on five dimensions: fit, buyability, reachability, service_opportunity, timing.
3. **Qualify** — high-fit leads get a Playwright deep-crawl: about, team, contact, recent posts. The agent reads pages like a salesperson would.
4. **Draft** — personalised cold email + LinkedIn note tailored to evidence the agent actually found.
5. **Learn** — every "good fit / bad fit" rating updates a per-user instruction summary (re-built every 3 signals) that the agent reads on the next hunt. After enough feedback the agent's scoring guidance shifts toward what *you* called good.

Your leads, drafts, and hunt history live in a local SQLite file at
`~/.local/share/huntova/db.sqlite`. No central server. No subscription.

## 60-second cold-email pipeline

```bash
huntova examples install solo-coach    # auto-seeds ICP + tone (10 playbooks)
huntova recipe run solo-coach          # ~3 minutes, ~25 leads
huntova outreach send --top 5 --dry-run        # preview AI-drafted emails
huntova outreach send --top 5 --max 5          # deliver via your SMTP
```

Three commands. Real prospects. Real personalised emails. Your AI key, your
SMTP, your machine. Nothing leaves your laptop except the search queries
hitting your SearXNG and the outbound emails hitting your provider.

## Full local-first cold-email stack

The commands below cover the same outbound steps you'd otherwise stitch
together across multiple commercial SaaS subscriptions — all running
against credentials *you* control, all on your machine:

```bash
huntova playbook install solo-coach      # 10 bundled ICP playbooks, auto-seed wizard
huntova hunt --max-leads 25              # find prospects (search → score → save)
huntova research --batch 5 --above 8     # deep-crawl 14 pages × 5 leads, rewrite openers
huntova outreach send --top 5 --research-above 8  # send research'd Step 1 (auto-enrols Step 2/3)
huntova sequence run --max 25            # Day +4 bump, Day +9 final, paused on reply
huntova inbox watch --interval 300       # IMAP poll + AI-classify replies (interested/OOO/etc)
huntova doctor --email                   # SPF / DKIM / DMARC / MX pre-flight
huntova pulse --since 7d                 # weekly self-coaching summary + next-action
huntova schedule print --target launchd  # emit OS-native scheduled-job for daily auto-runs
```

| Outbound capability | Typical SaaS pricing tier | Huntova command |
|---------------------|---------------------------|-----------------|
| Multi-page personalisation crawl | enterprise tier (~$300+/mo) | `huntova research` |
| Reply detection + AI triage | mid-tier seat-based (~$80–200/seat/mo) | `huntova inbox watch` |
| Multi-step follow-up sequence | mid-tier seat-based (~$99/seat/mo) | `huntova sequence run` |
| Deliverability pre-flight | dedicated SaaS (~$30/mo) | `huntova doctor --email` |
| Coaching / KPI summary | mid-tier (~$99/mo) | `huntova pulse` |
| Scheduled-job autopilot | mid-tier (~$97/mo) | `huntova schedule print` |

> Pricing references are typical commercial-tool ranges as published by
> the vendors at time of writing; see each vendor's site for current
> pricing. References are illustrative comparisons; Huntova is not
> affiliated with any third-party tool listed.

### Autonomous daily mode

After the one-time setup, wire the cadence to run itself:

```bash
huntova schedule print --target launchd --at 09:00 \
  > ~/Library/LaunchAgents/com.huntova.daily.plist
launchctl load ~/Library/LaunchAgents/com.huntova.daily.plist
```

(Linux: `--target systemd` outputs a user timer + service.) Now
`huntova sequence run`, `huntova inbox check`, and `huntova pulse`
fire daily on schedule. Hands-off cold outreach against your own
keys, on your own machine.

Other useful commands:

```bash
huntova playbook ls                       # 10 bundled playbooks (alias: examples)
huntova research <id>                     # deep-research one lead, rewrite the opener
huntova research --batch 5 --above 8      # batch-research the top 5 unsent leads
huntova ls --status replied               # quick filter shortcut
huntova ls --reply-class interested       # only the IMAP-classified positive replies
huntova ls --min-fit 8                    # only fit ≥ 8 leads
huntova share --top 10 --title "Q2"       # mint a /h/<slug> URL of top leads
huntova share status <slug>               # how many people clicked it
huntova pulse --since 30d                 # 30-day self-coaching summary
huntova sequence status                   # how many leads at each follow-up step
huntova inbox setup                       # one-time IMAP cred capture for reply detection
huntova schedule print                    # emit cron / launchd / systemd snippet
huntova hunt --explain-scores             # show fit/buy/timing/reach breakdown per lead
huntova doctor                            # diagnostic — verifies install, plugins, AI key
huntova doctor --email                    # deliverability pre-flight (dnspython bundled)
huntova plugins create my-crm-sink        # scaffold a plugin in ~/.config/huntova/plugins/
```

## Configuration

After `huntova init` you'll have:

| Path | Contents |
|------|----------|
| `~/.config/huntova/config.toml` | `preferred_provider`, optional non-secret defaults |
| `~/.config/huntova/secrets.enc` | Fernet-encrypted API key (fallback if no OS keychain) |
| `~/.local/share/huntova/db.sqlite` | All leads, hunts, settings, learning data |

API keys are stored in your OS keychain when available (macOS Keychain,
Windows Credential Manager, Linux Secret Service). Falls back to the
encrypted file at `~/.config/huntova/secrets.enc`. Last resort is a
0600-locked plaintext file at `~/.config/huntova/secrets.json` with a
warning.

### Override via env vars

| Variable | Purpose |
|----------|---------|
| `HV_ANTHROPIC_KEY` | Anthropic Console key (default provider — Claude) |
| `HV_GEMINI_KEY` | Google AI Studio key |
| `HV_OPENAI_KEY` | OpenAI Platform key |
| `HV_AI_PROVIDER` | `anthropic` (default) / `gemini` / `openai` / 10 more — see `huntova onboard` |
| `APP_MODE` | `local` (default for CLI) / `cloud` |
| `HUNTOVA_DB_PATH` | Override SQLite path |
| `DATABASE_URL` | If set, uses Postgres instead of SQLite |
| `SEARXNG_URL` | SearXNG instance for web search |

### Self-host SearXNG (recommended)

Huntova searches the web via [SearXNG](https://searxng.github.io/searxng/),
a privacy-respecting meta-search engine. Public instances mostly
disable the JSON API to prevent abuse, so the cleanest setup is to
run SearXNG yourself.

The fastest path is Docker:

```bash
docker run -d --restart=always \
  --name=searxng \
  -p 8888:8080 \
  -v "$HOME/.config/huntova/searxng:/etc/searxng" \
  -e BASE_URL=http://localhost:8888 \
  -e INSTANCE_NAME=huntova-search \
  searxng/searxng:latest
```

Then enable the JSON API by editing
`~/.config/huntova/searxng/settings.yml` and adding `json` to
`search.formats`. Restart the container.

Set `SEARXNG_URL=http://127.0.0.1:8888` (or run `huntova init` and
paste the URL when prompted) and Huntova will use it.

## Commands

```
# First-run + setup
huntova onboard          # rich TUI wizard — picks a provider, saves the key, opens the dashboard
huntova doctor           # full diagnostic — providers, SearXNG, plugins, AI key probe
huntova status           # one-screen operational dashboard (server, daemon, providers, last hunt)
huntova test-integrations # live probe of every configured provider + SearXNG

# Daily use
huntova                  # alias for `huntova serve`
huntova serve            # boot local dashboard (default port 5050)
huntova hunt             # one-shot headless hunt — streams leads to terminal
huntova ls [--filter X]  # list saved leads (table or json, substring/field-prefix filter)
huntova lead <id>        # full detail for one lead (use --by-org for partial match)
huntova rm <id>          # permanently delete one lead (use --yes to skip prompt)
huntova history          # list recent hunt runs (id, status, leads, queries)
huntova export           # CSV/JSON export to stdout

# Outreach
huntova outreach send    # AI-draft personalised cold emails for top leads, send via SMTP
huntova research <id>    # deep-research one lead (14-page crawl), rewrite the opener
huntova sequence run     # send Day +4 bump + Day +9 final to enrolled leads
huntova sequence status  # how many leads at each step
huntova sequence pause --lead-id <id>  # pause / resume one lead's cadence
huntova inbox setup      # capture IMAP creds for reply detection
huntova inbox check      # one-shot poll, prints scanned + matched count
huntova inbox watch      # daemon loop, auto-pauses cadences on reply

# Sharing + recipes
huntova share            # mint a public /h/<slug> link
huntova share status <slug> # view-count for a published share
huntova examples ls      # 10 bundled playbooks (auto-seed ICP + tone + queries)
huntova examples install <name> # adopt a playbook in one command
huntova recipe run <name>       # run a recipe end-to-end

# Configuration
huntova config show      # render the active config (secrets redacted)
huntova config edit      # open config.toml in $EDITOR
huntova config get <key> # read a single key
huntova config set <key> <value> # write a single key

# Plugins
huntova plugins          # list discovered plugins
huntova plugins create <name> # scaffold ~/.config/huntova/plugins/<name>.py
huntova plugins install <name> # one-command install from the registry
huntova plugins contribute    # registry PR flow shortcut

# Daemon (optional — survives reboots)
huntova daemon install   # install launchd (macOS) or systemd --user (Linux) unit
huntova daemon status    # current daemon state
huntova daemon logs      # tail the unit's logs

# Metadata
huntova metrics show     # in-app analytics for the dashboard tab
huntova update           # upgrade to latest via pipx
huntova version          # print version
```

### Browsing leads from the terminal

```bash
huntova ls --filter "country:Germany"   # field-prefixed filter
huntova ls --filter aurora              # substring scan across common text fields
huntova lead L3                          # full detail for one lead
huntova lead "aurora" --by-org           # partial-org-name lookup
huntova export --format csv > leads.csv  # pipe-friendly export
```

### Sharing hunts (the growth loop)

```bash
huntova share --top 10 --title "Q2 prospects"   # mints a public /h/<slug> URL
```

The shared page renders every lead with its **Proof Pack** — quoted
evidence, source URLs, freshness timestamp. The bottom half of leads
is visually blurred with a one-click `huntova hunt --from-share <slug>`
command that lets the recipient install Huntova and reproduce the
exact same hunt locally. Sticky bottom bar with `pipx install huntova`
copy button on every share page.

```bash
huntova hunt --from-share HYgJouz4J1k    # fetch the share, adopt its country set, run a fresh hunt
```

### Plugins (the moat)

```bash
huntova plugins                   # list discovered plugins
huntova plugins create my-crm     # scaffold ~/.config/huntova/plugins/my_crm.py
```

Plugins hook into the agent lifecycle: `pre_search`, `post_search`,
`pre_score`, `post_score`, `post_qualify`, `post_save`, `pre_draft`,
`post_draft`. They're loaded from `~/.config/huntova/plugins/*.py`
or as published packages declaring an `huntova.plugins` entry point.

**9 bundled reference plugins ship in the wheel** (load on every run; opt out with `HV_DISABLE_BUNDLED_PLUGINS=1`):

- **`csv-sink`** — append every saved lead to a local CSV file. Configure via `[csv_sink] path = "~/leads.csv"` in `config.toml` or `HV_CSV_SINK_PATH` env. No-op when neither is set.
- **`dedup-by-domain`** — drop search results from domains seen in the last 30 days (configurable via `[dedup] window_days = 30`). Eliminates the #1 noise source in multi-query hunts.
- **`slack-ping`** — POST to a Slack incoming webhook on each new lead. Configure via `[slack_ping] webhook_url = "..."` or `HV_SLACK_WEBHOOK_URL` env.
- **`discord-ping`** — POST a brief embed to a Discord incoming webhook on each new lead. Configure via `discord_webhook_url` (Settings → Integrations) or `HV_DISCORD_WEBHOOK_URL` env.
- **`telegram-ping`** — send a short message to a Telegram chat via the bot API on each new lead. Requires both `telegram_bot_token` and `telegram_chat_id` (Settings → Integrations) or env vars `HV_TELEGRAM_TOKEN` / `HV_TELEGRAM_CHAT_ID`.
- **`whatsapp-ping`** — send a WhatsApp message via Twilio on each new lead. Requires `twilio_account_sid`, `twilio_auth_token`, `twilio_whatsapp_from`, `whatsapp_to` (Settings → Integrations) or env vars `HV_TWILIO_SID` / `HV_TWILIO_TOKEN` / `HV_TWILIO_WHATSAPP_FROM` / `HV_WHATSAPP_TO`.
- **`generic-webhook`** — POST a JSON payload to a user-configured webhook on each saved lead. Configure via top-level `webhook_url` (Settings → Webhooks) or `HV_WEBHOOK_URL` env. HMAC-SHA256-signs the body with `HV_WEBHOOK_SECRET` (keychain or env) if set; signature lands in the `X-Huntova-Signature: sha256=<hex>` header.
- **`recipe-adapter`** — reads `HV_RECIPE_ADAPTATION` env, applies winning_terms / suppress_terms / added_queries to the query list before search.
- **`adaptation-rules`** — applies AI-generated scoring rules from the recipe adaptation card. Closes the outcome→adapt→hunt loop.

Browse the full registry of community plugins (with capability disclosures
so you can audit what each plugin can do before installing) at
[huntova.com/plugins](https://huntova.com/plugins).

Example: a Slack-pinger plugin —

```python
class SlackPing:
    name = "slack-ping"
    version = "0.1.0"
    def post_save(self, ctx, lead):
        import urllib.request, json
        url = "https://hooks.slack.com/services/..."
        body = json.dumps({"text": f"new lead: {lead.get('org_name')}"}).encode()
        urllib.request.urlopen(url, data=body, timeout=5)
```

Drop it in `~/.config/huntova/plugins/slack_ping.py` and every saved
lead pings your Slack. No restart needed for the next hunt.

### Verifying setup

```bash
huntova doctor
```

Reports:
- Python + config paths
- Configured providers + secrets backend (keyring / encrypted file / plaintext)
- SearXNG reachability + JSON API status
- **Live 5-token round-trip to your AI key** — confirms it works, not just that it's set
- Per-env-var status

`--quick` skips the network call.

### Headless hunt example

```bash
huntova hunt --countries Germany,France --max-leads 10
```

Output streams as the agent runs:

```
[huntova] hunting in 2 countries: Germany, France
[huntova] cap: 10 leads
[huntova] streaming to ~/.local/share/huntova/db.sqlite (Ctrl-C to stop)

  · Loading hunt brain
  · Generating search queries
  ✓ [9/10] Aurora Studios            · Germany  · aurora-studios.de
  ✓ [8/10] Tessera Marketing         · France   · tessera-mkt.com
  ✓ [9/10] Helio Production          · Germany  · helio-prod.de
  ...

[huntova] hunt completed: 10 leads
[huntova] top 3:
  · [9/10] Aurora Studios — Mid-size production house, recently launched a brand campaign
  · [9/10] Helio Production — Recurring event series, gap in their post-production pipeline
  · [8/10] Tessera Marketing — Boutique agency hiring for content roles

[huntova] view in dashboard: `huntova serve`
```

`--verbose` unlocks the full log + thought stream. Ctrl-C cleanly
stops the agent. Both `huntova hunt` and `huntova serve` share the
same SQLite file, so leads found via either path show up in both.

## Providers

| Provider | When to pick | Get a key |
|----------|--------------|-----------|
| **Claude (Anthropic)** (default) | Highest-quality scoring, best email drafts. | <https://console.anthropic.com/settings/keys> |
| **Gemini** | Free tier, fast scoring, good defaults | <https://aistudio.google.com/apikey> |
| **OpenAI** | Broad model selection, GPT-5 reasoning | <https://platform.openai.com/api-keys> |

13 providers in total — see `huntova onboard` for the full list (cloud,
local-AI servers, custom OpenAI-compatible endpoints).

Switching providers later: re-run `huntova onboard --force` or edit
`~/.config/huntova/config.toml`.

## Status

**Alpha.** The codebase is being pivoted from a hosted SaaS to this
CLI shape. Core agent (search/score/qualify/draft) is stable; the
pivot is rebuilding the storage + auth + provider edges. Track
progress on the [issues page](https://github.com/enzostrano/huntova-public/issues).

## Smoke test

```bash
APP_MODE=local DATABASE_URL= HV_ANTHROPIC_KEY=fake \
    python tools/smoke_test_local.py
```

25 checks covering runtime capabilities, policy, DB driver, schema,
FastAPI routes, auto-login, feature unlocks, and the /download page.

## License

AGPL-3.0-or-later. See [LICENSE](./LICENSE).

## Credits

Built and maintained by [@enzostrano](https://github.com/enzostrano).
