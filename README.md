# Huntova

**Find your next 100 customers from the command line.** Open-source, BYOK
lead-gen agent. Hunts the live web, scores prospects with your AI key,
proves every match with verbatim evidence — locally, on your machine.

> Replaces Apollo / Clay / Hunter. No subscription. Your data stays on your
> disk. Your AI key never leaves your machine.

```bash
pipx install huntova
huntova init                          # zero-interactive — creates config dir
export HV_GEMINI_KEY="..."            # https://aistudio.google.com/apikey
huntova hunt --max-leads 5            # first lead in ~30s
```

Prefer a friendly prompt instead of `export`? Run `huntova init --wizard`.

Or via curl:

```bash
curl -fsSL https://huntova.com/install.sh | sh
```

Or by cloning:

```bash
git clone https://github.com/enzostrano/huntova.git
cd huntova
pip install -e .
huntova init
huntova serve
```

## Try it without installing

Paste your ICP at **[huntova.com/try](https://huntova.com/try)** — gets a
3-lead Proof Pack at `/h/<slug>` in 30s, no install, no sign-up.

> **Note:** /try runs in **Preview Mode** with honestly-labelled synthetic
> shape data, so the public site survives high traffic without IP-banning
> our SearXNG sidecar. The local CLI runs 100% against the live web with
> your own AI key.

## What it does

1. **Sweep** — SearXNG-powered web search across Google, Bing, DuckDuckGo. 50–80 AI-generated queries per hunt, filtered for noise.
2. **Score** — every prospect graded on five dimensions: fit, buyability, timing, reachability, decision-making authority.
3. **Qualify** — high-fit leads get a Playwright deep-crawl: about, team, contact, recent posts. The agent reads pages like a salesperson would.
4. **Draft** — personalised cold email + LinkedIn note tailored to evidence the agent actually found.
5. **Learn** — every "good fit / bad fit" rating refines the next hunt's query set and scoring rules.

Your leads, drafts, and hunt history live in a local SQLite file at
`~/.local/share/huntova/db.sqlite`. No central server. No subscription.

## 60-second cold-email pipeline

```bash
huntova examples install tech-recruiting        # 25 starter queries
huntova recipe run tech-recruiting              # ~3 minutes, ~25 leads
huntova outreach send --top 5 --dry-run         # preview the AI-drafted emails
huntova outreach send --top 5 --max 5           # actually deliver via your SMTP
```

Three commands. Real prospects. Real personalised emails. Your AI key, your
SMTP, your machine. Nothing leaves your laptop except the search queries
hitting your SearXNG and the outbound emails hitting your provider.

Other useful commands:

```bash
huntova examples ls                       # 4 starter recipes (agencies, SaaS, tech-recruiting, ecommerce)
huntova share --top 10 --title "Q2"       # mint a /h/<slug> URL of top leads
huntova share status <slug>               # how many people clicked it
huntova hunt --explain-scores             # show fit/buy/timing/reach breakdown per lead
huntova doctor                            # diagnostic — verifies install, plugins, AI key
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
| `HV_GEMINI_KEY` | Google AI Studio key |
| `HV_ANTHROPIC_KEY` | Anthropic Console key |
| `HV_OPENAI_KEY` | OpenAI Platform key |
| `HV_AI_PROVIDER` | `gemini` (default) / `anthropic` / `openai` |
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
huntova                  # alias for `huntova serve`
huntova serve            # boot local dashboard (default port 5050)
huntova hunt             # one-shot headless hunt — streams leads to terminal
huntova init             # interactive first-run wizard
huntova ls [--filter X]  # list saved leads (table or json, substring/field-prefix filter)
huntova lead <id>        # full detail for one lead (use --by-org for partial match)
huntova rm <id>          # permanently delete one lead (use --yes to skip prompt)
huntova history          # list recent hunt runs (id, status, leads, queries)
huntova plugins          # list discovered plugins (or `create <name>` to scaffold one)
huntova export           # CSV/JSON export to stdout
huntova share            # mint a public /h/<slug> link
huntova doctor           # diagnostic + live AI key probe
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

**3 bundled reference plugins ship in the wheel** (load on every run; opt out with `HV_DISABLE_BUNDLED_PLUGINS=1`):

- **`csv-sink`** — append every saved lead to a local CSV file. Configure via `[csv_sink] path = "~/leads.csv"` in `config.toml` or `HV_CSV_SINK_PATH` env. No-op when neither is set.
- **`dedup-by-domain`** — drop search results from domains seen in the last 30 days (configurable via `[dedup] window_days = 30`). Eliminates the #1 noise source in multi-query hunts.
- **`slack-ping`** — POST to a Slack incoming webhook on each new lead. Configure via `[slack_ping] webhook_url = "..."` or `HV_SLACK_WEBHOOK_URL` env.

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
| **Gemini** (default) | Free tier, fast scoring, good defaults | <https://aistudio.google.com/apikey> |
| **Claude (Anthropic)** | Highest-quality scoring, best email drafts | <https://console.anthropic.com/settings/keys> |
| **OpenAI** | Broad model selection, GPT-5 reasoning | <https://platform.openai.com/api-keys> |

Switching providers later: re-run `huntova init --force` or edit
`~/.config/huntova/config.toml`.

## Status

**Alpha.** The codebase is being pivoted from a hosted SaaS to this
CLI shape. Core agent (search/score/qualify/draft) is stable; the
pivot is rebuilding the storage + auth + provider edges. Track
progress on the [issues page](https://github.com/enzostrano/huntova/issues).

## Smoke test

```bash
APP_MODE=local DATABASE_URL= HV_GEMINI_KEY=fake \
    python tools/smoke_test_local.py
```

22 checks covering runtime capabilities, policy, DB driver, schema,
FastAPI routes, auto-login, feature unlocks, and the /download page.

## License

AGPL-3.0-or-later. See [LICENSE](./LICENSE).

Built by [@enzostrano](https://github.com/enzostrano), with a lot of
help from Anthropic Claude (Opus 4.7) running on Claude Code.
