# Huntova launch plan — Tuesday May 5, 2026

This is the operational launch playbook synthesised from the 3-tab Perplexity
brainstorm rounds (Huntova engineering strategy, Gemini 3.1 Pro GTM, Kimi K2.6 architecture).
**Date and angle are locked.** Read this top-to-bottom before launch day.

## Date and angle

- **Launch:** Tuesday, May 5, 2026 at **10:00 AM ET**.
- **Channel sequence:** Show HN first, X/cold-email afterwards.
- **Cold-email-50 runs the day BEFORE** (Monday May 4 morning) — see § Day-1 schedule.
- **Angle:** "Paste your ICP, get a preview Proof Pack in the browser, then fork the exact hunt locally."

## Pre-launch gate

Launch is gated on **`huntova doctor` exit-code 0** on a clean wheel install across
the 9-cell matrix (ubuntu-latest / macos-latest / windows-latest × Python 3.11 / 3.12 / 3.13).

YAML stashed at `docs/workflows/pre-launch-smoke.yml` — Enzo copies it into
`.github/workflows/` when ready (Claude can't push to that path). Run it manually
the morning of launch via `workflow_dispatch`. If green on all 9 cells: ship. If any
red: fix, re-run, hold until green.

## PyPI publishing — the launch's hidden critical-path (GPT round 75)

**The biggest hidden risk to May 5 is install-path integrity, NOT marketing.** Every
bullet of the launch story assumes `pipx install huntova` Just Works. If the package
isn't actually published to PyPI by Tuesday morning, the install collapses to "git
clone and improvise" — fatal on Hacker News where friction kills curiosity fast.

The workflow YAML is stashed at `docs/workflows/publish.yml` (tag-triggered,
PyPI trusted-publishing via `pypa/gh-action-pypi-publish`, no API tokens needed).

**Pre-launch steps (Saturday May 3 — Sunday May 4):**

1. Claim/verify the `huntova` package name at https://pypi.org/manage/projects/ —
   if the name is taken, escalate immediately (rename or contact PyPI support).
2. Configure the trusted publisher for the repo:
   https://pypi.org/manage/account/publishing/
   - PyPI project: `huntova`
   - GitHub owner: `enzostrano`, repo: `huntova`
   - Workflow: `publish.yml`
   - Environment: `pypi`
3. Copy `docs/workflows/publish.yml` to `.github/workflows/publish.yml` via the
   GitHub UI (Claude can't push to that path).
4. Add a GitHub Environment named `pypi` to the repo (Settings → Environments).
5. Cut the first release: `git tag v0.1.0a1 && git push origin v0.1.0a1`.
   The Action runs, builds sdist + wheel, publishes to PyPI.
6. **Verify install on TWO clean machines (macOS AND Linux).**

Verification commands per machine:

```bash
pipx install huntova         # must succeed, no errors
huntova version              # must print 0.1.0a1
huntova init                 # zero-interactive, prints filesystem state, exits 0
huntova doctor --quick       # must exit 0
huntova hunt --max-leads 1   # must save at least 1 lead to db.sqlite
                             # (uses your wizard ICP — `huntova hunt`
                             #  has no positional query argument; the AI
                             #  generates 60-80 queries from the wizard
                             #  profile every run)
```

If ANY of these fails on EITHER machine, **launch is blocked.** This is the single
highest-leverage pre-launch verification. Fix and re-run.

**Emergency fallback** (paste in HN thread + README backup section):

```
If PyPI propagation bites you, use:
  pipx install git+https://github.com/enzostrano/huntova
```

Backup only — never the headline install path.

## What Enzo physically does (24-48h before launch)

In this exact order:

1. **PyPI publishing pipeline (GPT round-75 critical-path).** See § PyPI
   publishing — the launch's hidden critical-path below for the full 6-step
   procedure. Verify on TWO clean machines (macOS + Linux) BEFORE moving on.
   Without this, the entire launch story breaks. Block on this until green.

2. **Record the 60s asciinema cast.** A real `huntova hunt` terminal session.
   Embed the SVG/GIF at the very top of the GitHub README. The web demo is
   synthetic; the README must prove the real code works.

3. **Run 50 manual hunts.** Generate 50 real `/h/<slug>` links — one per
   marketing-agency target — for the Monday cold-email blast. This will take
   ~2 hours and stress-tests the CLI one last time.

4. **Buy huntova.com & set DNS.** Point at the Railway app. No one on HN
   clicks `*.up.railway.app` — it screams "weekend project, abandoned next week."

5. **Set API billing hard limits.** Hard $20 cap on `HV_DEMO_AI_KEY` in the Google
   Cloud / Anthropic console. The /try quota counter is nice, but HN users
   *will* try to bypass the frontend with cURL. Protect the bank account.

6. **Update GitHub repo "About".** Set description to:
   > Find your next 100 customers from the command line. An open-source, BYOK
   > lead generation agent. Replaces Apollo/Clay.

   Add tags: `python`, `lead-generation`, `cli`, `ai`, `osint`.

7. **Pre-write 3 defensive HN replies.** Drop these into a notepad so they
   can be copy-pasted instantly:
   - **"Google will block this immediately with CAPTCHAs."** → Explain the SearXNG
     proxy rotation and per-domain delay logic.
   - **"Why Python and not Go/Rust?"** → AI ecosystem integration and pandas/data
     manipulation make Python the right call for an AI agent.
   - **"This is just spam automation."** → Frame as high-fidelity, low-volume
     qualification (5-dimensional scoring, evidence quotes, reachability waterfall)
     vs spray-and-pray.

Skip the Discord server (looks pathetic with 3 people). Skip the @huntova_app X
account (use @enzostrano — people buy from founders).

## Cold-email-50 (Monday May 4 morning, BEFORE HN)

**Segmentation (Gemini round 73):**
- US + UK only — highest software budgets, easiest cold targets.
- 2–15 employees — founders still check their own inbox + feel $150/mo pain.
- 25× **B2B SaaS Growth Agencies** (`"B2B SaaS marketing agency" OR "B2B growth agency"`).
- 25× **Technical Recruiting Agencies** (`"technical recruiting agency" OR "devops staffing"`).

**Sourcing:** **Eat your own dog food.** Run Huntova itself for the 50 — when
recipients ask "where did you get my email?", reply "I used the open-source
CLI I'm pitching you." Operationally:

```bash
huntova hunt --countries USA,UK --max-leads 50
```

(For canned query lists per playbook, save them as a recipe: `huntova recipe save <name> --queries "..."`. The `hunt` command itself doesn't take `--queries`; the AI generates 60–80 ICP-specific queries from your wizard profile every run.)

**Subject (Gemini round 75 stress-test):** ~~`Ran a local scraper for ...`~~ — too
spam-shaped (`scraper` + personalisation token + `leads` is a near-perfect bulk
outbound match). New subject:

> `Open-source alternative to Apollo for [Their Agency Name]`

Trigger the agency-owner pain point (Apollo) without hitting filter keywords.
Reads like a peer-to-peer tip, not an SDR sequence.

**Send pacing (Gemini round 75):** stagger the 50 in batches of 10 every 10
minutes between 08:30–09:30 ET so Google Workspace doesn't flag your IP for
velocity. Same domain × same hour × identical subjects = guaranteed bulk flag.

**Body (round 75 tweaked):**

```
Hey [Name],

I noticed [Their Agency Name] does a lot of outbound. I got tired of
paying Apollo/Clay $150/mo for stale data, so I built an open-source
alternative that runs locally. You just plug in your own API keys.

To test it, I ran a 45-second terminal query for your ideal targets
[Target Role, e.g., "Series A eCommerce founders"].

Here is the raw data output: [Link to personalized /h/<slug>]

The CLI is completely free on GitHub if your team wants to use it.

Cheers,
Enzo
```

**Conversion mechanic:** if 2-3 reply Monday saying "this is sick", reply with
*"Thanks! I'm actually launching it on Hacker News tomorrow at 7 AM PST,
would love your thoughts there."* — converts cold leads into HN voters.

## HN headline + first comment (Tuesday May 5, 10:00 ET)

**Headline:**
> Show HN: I open-sourced my $150/mo lead-gen SaaS to run locally (BYOK)

**First comment** (post immediately after the thread goes live — Gemini's verbatim
copy, with Preview Mode disclosed up front to disarm the synthetic-demo critique):

> Hey HN, Enzo here. I got tired of Apollo credits and SaaS lock-in, so I
> ripped the paywall out of my lead-gen app and open-sourced it as a Python
> CLI. It uses a local SearXNG sidecar to scrape the live web, and your own
> Anthropic/Gemini keys to score prospects on 5 dimensions.
>
> Because `pipx install` is a high barrier just to see how the scoring engine
> works, I wired up a zero-install scratchpad at [huntova.com/try]. **Note on
> the web demo:** It runs in "Preview Mode" with honestly-labeled synthetic
> SERP data. I did this so the site survives the HN hug-of-death without my
> servers getting instantly IP-banned by Google. But if you install the CLI
> locally, it runs 100% against the live web.
>
> Happy to answer questions about the Postgres-to-SQLite pivot, bypass
> heuristics, or the scoring architecture.

## Day 1 hour-by-hour (Tuesday May 5, all times ET)

Note: round-73 refinement moves the X thread to **before** the HN drop
(09:30 ET) because X penalises external-link tweets — see § Day 1 X thread.

| Time | Action |
|---|---|
| 7:00–8:00 | Final smoke pass: clean install, /try flow, /h/<slug> share, OG card render, metrics dashboard sanity. Doctor green on all 9 CI cells. |
| 8:00–9:00 | Preload launch tabs. Finalise HN title/body/first reply. Prep X post and email snippets. Confirm rate limits and alerting. |
| **9:30** | **Drop the 4-tweet X thread.** No external links yet. |
| 9:45 | **Deploy freeze** unless severity-1. |
| **10:00** | **Post Show HN.** Drop first comment immediately. |
| 10:05 | Reply to T4 with the actual HN link once live. |
| 10:30–14:00 | **Live in the HN thread.** Refresh every 3 min. Reply to every technical question. Ignore X. |
| 14:00 | Batch X replies. Quote-tweet the best HN comment ("Front page of HN! Feedback on the BYOK architecture is amazing."). |
| 11:30–14:00 (parallel) | If HN is stable, the 50 cold-emails sent Monday morning are now showing replies — answer with "thanks, the HN thread is here" so the threads converge. |
| 18:00 | Final standalone tweet: "Over [X] leads generated today on the Huntova /try scratchpad. Open-source is eating B2B data." |
| 17:00–19:00 | Summarise objections from HN. Ship only tiny fixes or copy clarifications. |

## Day 1 X thread (Gemini round 73 — NOT a single tweet)

X penalises tweets containing external links. Use a **4-tweet thread**, drop
at **09:30 ET** (before HN at 10:00 ET):

1. **Hook + visual** — round-71 verbatim copy + 60s asciinema cast. **No link.**
   > Stop paying $150/mo for B2B data subscriptions. I open-sourced a
   > local-first lead generator. You bring your own API keys, it runs
   > from your terminal, and finds your exact ICP for pennies.

2. **Proof** — "I used it to scrape 50 marketing agencies this morning. Cost
   me $0.42 in Anthropic credits. It extracted the founder, found their
   email, and scored their buyability. Example output:" + screenshot of one
   `/h/<slug>/og.svg`.

3. **/try CTA** — "I know `pipx install` is a high barrier if you just want
   to see the AI scoring engine. So I built a zero-install scratchpad. Run
   a 30-second headless hunt right now (no signup): https://huntova.com/try"

4. **HN CTA** (posted right after the HN link is live, ~10:05 ET) — "Huntova
   is live on Hacker News today. Would love feedback on the Postgres-to-SQLite
   architecture or the scraping heuristics. [HN link]"

## 48-hour @enzostrano content arc (GPT round 73)

Three-beat narrative: **problem → artifact → proof → launch.** Founder-led
content works as one continuous build, not random promo bursts.

| When (ET) | Post | Length | Asset |
|---|---|---|---|
| Sat May 3 | **Thesis** — "Why I rebuilt Huntova as a local-first CLI instead of another lead-gen SaaS" | 180–240 chars | none |
| Sun May 4 (AM) | **Artifact** — one expanded Proof Pack with "this is what a lead should look like" | 180–280 + screenshot/GIF | screenshot |
| Sun May 4 (PM) | **Build** — recipes, rerun+diff, share/fork: "a lead search should be reproducible" | 220–320 + terminal clip | terminal clip |
| Mon May 5 (PM) | **Founder dogfood** — "Ran this for my own agency ICP and sent 5 emails" | 250–350 + `/h/<slug>` + email screenshot | `/h/<slug>` + email |
| Tue May 6 09:00 | **Pre-launch** — "Posting Huntova to HN in 60 minutes; paste your ICP, get a preview Proof Pack, then fork it locally" | 140–220 | none |
| Tue May 6 09:30 | **Launch thread (4 tweets)** — see above | per-tweet | asciinema + OG |
| Tue May 6 12:00 | **Objection-handling** — answer the top 2 HN questions: Preview Mode + why local-first | 250–400 | none |

## "One real success story" — founder dogfood (GPT round 73)

By **Monday EOD**, Enzo needs the following live as the public proof:
- One real `huntova hunt` for **his own agency's ICP**, published as a
  public `/h/<slug>`.
- One selected lead expanded with strong proof + reachability + obvious fit.
- 5 real emails sent using Huntova-assisted drafts. 1 screenshot of the
  sent message + 1 screenshot of the draft before/after his edit.
- Headline regardless of replies: *"I used Huntova to go from ICP to 5 real
  outbound emails in under an hour."*

Don't wait for a booked call. The story is the workflow, not the revenue.

## Asciinema cast script (Kimi round 73)

Target **55 seconds**, end in the terminal (don't cut to browser — README
already shows the share page above).

ICP: `"boutique creative agencies in Berlin hiring motion designers"` —
narrow, European TLDs, hiring signals, fits 60s.

```
Step 1 (3s): $ huntova init
             [huntova] initialised
                       · config:    ~/.config/huntova/config.toml
                       · db:        ~/.local/share/huntova/db.sqlite (will be created on first hunt)
             ...

Step 2 (2s): $ export HV_GEMINI_KEY="AIza...redacted..."

Step 3 (35s): $ huntova hunt --countries DE --max-leads 6
              # ICP comes from the wizard / config; see also `huntova recipe save <name> --queries "..."`
              Query: boutique creative agencies Berlin motion designers
              SearXNG: 14 raw results
              Dedup: 9 unique domains
              Scoring... ━━━━━━━━━━━━━━━━━━━━━━━ 100% 6 leads

              Name              Domain               Score   Email
              ──────────────────────────────────────────────────────────
              Strobe Media      strobemedia.de       8.4     hello@strobemedia.de
              KONZEPT.          konzept.agency       7.9     jobs@konzept.agency
              Framehouse        framehouse.berlin    7.2     kontakt@framehouse.berlin
              Noisy Pictures    noisypictures.io     6.8     hi@noisypictures.io
              Pixelkind         pixelkind.de         6.1     info@pixelkind.de
              Lumenstudio       lumenstudio.co       5.5     team@lumenstudio.co

              Saved 6 leads to db.sqlite.

Step 4 (5s): $ huntova ls --limit 3

Step 5 (5s): $ huntova share --top 3 --title "first 3 leads"
              Packaged 3 leads → https://huntova.com/h/abc123
```

Pause 1.5s after each command output finishes rendering. Mask the API key
in post-production. Don't show the browser.

## What to monitor (5 numbers, all day)

1. `/try` success rate
2. Median `/api/try` latency
3. `/try` → install click-through
4. `pipx install` / `doctor` failure reports
5. Share-page **fork** CTA click rate

## Failure modes

**If /try slows down:** flip the homepage CTA text to *"Try Preview Mode —
queues may be slow, install locally for the full engine"*. Pin an HN comment
saying demand is high and the local CLI is unaffected. If the slowdown is
severe, lower /try throughput rather than degrading the install path.

**If HN stalls** (post underperforms in the first 90 minutes): **DO NOT** repost
or panic-tweak the title. Shift into operator mode — push the X thread, send
the targeted founder/SDR emails with the HN link anyway, and use whatever
comments you got to refine the first-reply framing for the next wave.

**If a savvy commenter still nukes Preview Mode** (despite the badge + label):
do NOT concede, do NOT defend the demo as a demo. **Reframe** (GPT round 73
verbatim):

> Fair push. /try is not meant to prove Huntova's live-web accuracy — it is
> meant to show the **output artifact**: Proof Packs, evidence layout,
> reachability, and the fork-to-local flow. The real product is the local
> CLI, which does live hunts, reruns, diffs, recipe memory, plugins, and
> exports under your own keys. That's why Preview Mode is capped, labeled,
> and structurally missing the core loops. I'd rather make the preview
> obviously incomplete than ask people to install a CLI blind.

**Don't pin top-level defensive comments** (Gemini round 73). HN rejects
founders who appear pre-emptively defensive. Wait for actual critics, then
deploy the 4 pre-written replies as in-thread responses. The community
upvotes the answer, which floats the thread to the top.

## Pre-written HN reply bank

Keep these in a local notepad. Drop them as in-thread replies to the
specific critic, not as top-level posts. Each tuned for a predicted
critique cluster.

### Reply 1 — "Google will block this with CAPTCHAs"

> Fair concern — but Huntova doesn't hit Google directly. It hits a
> SearXNG sidecar (self-hosted by you) that aggregates Google + Bing +
> DuckDuckGo + a few smaller engines, and the JSON API surface stays
> stable as long as you self-host. Per-domain delays (default 3s) and a
> 30-result-per-query cap keep it well under any individual engine's
> rate limit. If Google does flag a SearXNG instance, you spin up a new
> one in 30s with `docker run searxng/searxng` and point Huntova at it.
> The agent isn't the scraper — SearXNG is. We're just the orchestrator
> on top.

### Reply 2 — "Why Python and not Go/Rust for a CLI?"

> The CLI ships fast either way (~30s install via pipx). The reason for
> Python is the AI ecosystem — every provider SDK is Python-first
> (anthropic, openai, google-genai), and the data manipulation
> (BeautifulSoup, lxml, JSON-LD parsing, structured-data extraction) is
> richest in Python. Playwright also has the best Python bindings of
> any non-Node language. Rewriting in Go/Rust would buy ~2x install
> speed and lose 6 months on the AI integration surface — not the
> trade-off I'd make for a tool whose value is the agent loop, not the
> binary size.

### Reply 3 — "This is just spam automation"

> The opposite, actually. Huntova's whole architecture is built around
> high-fidelity / low-volume — it's a 5-dimensional scoring agent
> (fit, buyability, reachability, service_opportunity, timing)
> with a Playwright deep-qualify pass on high-fit leads. A typical hunt
> returns 5-10 *qualified* prospects from 60-80 search queries, each
> with verbatim evidence quotes. Compare to Apollo's bulk export of
> 10,000 unverified rows — that's the spray-and-pray motion this tool
> exists to replace. If you wanted a spam tool, you'd use a spam tool;
> this is what you build when you've been on the receiving end of one.

### Reply 4 — "Synthetic /try is misleading" (GPT round 73 verbatim)

> Fair push. /try is not meant to prove Huntova's live-web accuracy — it
> is meant to show the **output artifact**: Proof Packs, evidence
> layout, reachability, and the fork-to-local flow. The real product is
> the local CLI, which does live hunts, reruns, diffs, recipe memory,
> plugins, and exports under your own keys. That's why Preview Mode is
> capped, labeled, and structurally missing the core loops. I'd rather
> make the preview obviously incomplete than ask people to install a
> CLI blind.

### Reply 5 (bonus) — "BYOK / Apollo with extra steps"

> The BYOK piece IS the architectural choice that makes Huntova
> local-first. Your AI key, your machine, your SQLite — Huntova never
> sees your queries or leads. Apollo charges $150/mo for a stale
> database that's already 3 months out of date the moment you query
> it. Huntova hunts the live web at request-time, scores with whatever
> model you trust (Gemini Flash for cheap, Claude Sonnet for accuracy,
> Ollama for fully-offline), and proves every match with verbatim
> evidence quotes. The moat is the proof, not the volume.

## Architecture readiness (Kimi)

- **All 8 plugin hooks live** in app.py: pre_search, post_search, pre_score,
  post_score, post_qualify, post_save, pre_draft, post_draft.
- **9 bundled plugins**: csv-sink, dedup-by-domain, slack-ping, discord-ping,
  telegram-ping, whatsapp-ping, generic-webhook, recipe-adapter,
  adaptation-rules. The last two close the v2.0 outcome→adapt→hunt loop
  end-to-end (queries via recipe-adapter pre_search, scores via
  adaptation-rules post_score).
- **`huntova doctor` hardened**: probes Playwright availability + Chromium
  install, SQLite data-dir writability, and returns non-zero exit on any
  critical failure (no providers, SearXNG unreachable, data dir unwritable,
  AI probe fail). This is the launch gate.

## Frontend readiness (Huntova engineering + Gemini)

- `/try` Preview Mode framing throughout: kicker, h1, lede, disclaimer, CTA,
  powered-by line all explicitly labeled. Disclaimer enumerates what /try
  does NOT do.
- `/try` prompt rewritten to look scraped-not-polished: imperfect grammar,
  mid-sentence truncation, regional TLD variation, varying email patterns,
  one 5-6 fit lead per batch for honest scoring spread.
- `/try` live quota counter: "X / 5 demos left this hour" with auto-refresh
  on 429. Disables submit when zero.
- `/h/<slug>` "Preview-generated sample" banner at top + same badge in OG
  image (the GPT round-72 counter-takedown for the synthetic-demo critique).
- `/h/<slug>` dynamic OG image at `/h/<slug>/og.svg` — 1200×630 terminal-
  styled with the visitor's ICP as a quoted query, top-3 lead names, fit
  scores colour-coded. Pasted-link unfurl is the billboard.
- `/h/<slug>` "Fork this hunt locally" CTA at the TOP of the page, command
  in mono with Copy button.

## Telemetry (post-launch wiring, opt-in only)

Three events, no more:
- `try_submit` — server-side, on POST /api/try success.
- `cli_init` — CLI, after `huntova init` succeeds (opt-in only).
- `cli_hunt` — CLI, after a hunt completes (opt-in only).

Opt-in via `huntova telemetry enable` (touches `~/.config/huntova/.telemetry`).
NOT an interactive prompt during init — that breaks scripting. Server endpoint
appends to a `metrics` SQLite table.

Skip og.svg fetches (Twitter/Discord scraper noise). Skip /try → /h/<slug>
redirect (redundant with try_submit). Skip doctor failures (you feel them in
support requests if they matter).

## v1.0 cut-line (post-launch goals, not launch blockers)

1. Truthful public core loop. ✅ /try is honest now.
2. Supported-platform install actually works. → CI smoke gate (Kimi YAML).
3. Five-minute blank-machine path proven. → README + asciinema cast.
4. One real success story. → Enzo's own first agency hunt.
5. Launch metrics wired. → 3 events above.

NOT v1.0 blockers: 5 verified plugins in registry; more landing-page polish.

## Success-metric trajectory (GPT rounds 74 + 75)

The **north-star is NOT GitHub stars.** It's `cli_hunt / cli_init` ratio +
**repeat hunts per user.** Stars tell you distribution worked; repeat hunts
tell you the product is becoming a habit.

But — critical refinement from GPT round 75 — north-star is the **week-1
truth, not the Wednesday truth.** Repeat-hunt data isn't visible until enough
time has passed. So split metrics by horizon:

**Tuesday → Wednesday LEADING indicators** (decide what to ship Wed):
- /try completion rate (visitor pastes ICP + clicks → /h/<slug> resolves)
- /try → install click-through rate
- cli_init success rate (telemetry event)
- doctor --quick failure reasons (from support DMs / GitHub issues)
- First cli_hunt completions (any user beyond Enzo)
- HN comment themes (positive / objection / confusion clusters)

**Week-1 LAGGING indicators** (decide whether the product has pull):
- Repeat hunts per user (≥2 separate days)
- Recipe saves
- share → fork loops
- Returning users

| Checkpoint | What success looks like |
|---|---|
| **Day 1** (Tue May 5 EOD) | 8K visitors, 1K /try runs, 120 install attempts, 50 cli_init, 20 cli_hunt, 250 GitHub stars, 40 HN comments, 10 inbound DMs |
| **Week 1** (May 12) | 600 stars, 300 cli_init, 120 cli_hunt, 25 users with hunts on 2+ separate days, 8 public Proof Packs from non-Enzo users, 3 community plugin convos |
| **Month 1** (June 5) | 1.5K stars, 1K cli_init, 300 users with 2+ hunts, 75 with 5+ hunts, 15 plugins (registry or pending), 5 serious agency teams using repeatedly, 2 inbound commercial convos |

## Phenomenon-scale risks (GPT round 74)

If launch goes BIGGER than expected, the top-2 risks to pre-stage:

**Risk #1: /try budget burn** — $20 cap on HV_DEMO_AI_KEY exhausts by 2pm ET.
- Mitigations:
  - Hard daily cap in the Google Cloud / Anthropic console (already in
    Enzo's pre-launch checklist).
  - Server-side **kill switch**: if budget hit, swap /try into "Preview
    Mode is saturated — fork locally now" fallback that returns a generic
    sample lead instead of calling the AI.
  - Queue mode for the last hour of budget headroom (don't 503 — display
    a "your hunt will run in 4 minutes" message).
  - Public copy on /try: "queues may be slow — install locally for the
    full engine".

**Risk #2: GitHub issues + DM overload** — bug reports + "how do I plug into
my CRM" questions flood Enzo before he can triage in real-time.
- Mitigations:
  - Issue templates today: `bug`, `install`, `plugin`, `question`.
  - DM auto-reply pointing to GitHub Discussions / Issues.
  - Pinned `KNOWN ISSUES / LAUNCH DAY` thread so duplicate reports
    consolidate.
  - Pre-write a canned "CRM integration via plugins — see csv-sink and
    huntova plugins create" reply Enzo can paste 30 times.

NOT in top-2: competitor replies (Apollo / Clay noticing). That's a signal of
relevance, not danger.

## Plan B if HN underperforms (Gemini round 74)

Page-3 within 2h, sub-1% /try → install conversion, no community engagement
by Wednesday morning? Move on the same week:

**Wednesday (T+1) 10:00 AM ET — Indie Hackers "Show IH":**
- Hook: *"I killed my $150/mo Apollo bill."*
- Frame: solo-founder cost-saving story, BYOK as the architectural choice.
- IH community is solo founders + small agencies who refuse to pay $1.8K/yr
  for Apollo. They WILL endure `pipx install` to save money.

**Thursday (T+2) 09:00 AM ET — Cold DM 50 sales/GTM X power users:**
- Hook (DM): *"Built an open-source Apollo killer."*
- Frame: B2B sales influencers regurgitate "Top 10 AI Prompts" content.
  An actual free tool gives them tutorial fodder + engagement bait.
- Body: *"You post a lot about outbound stacks. Open-sourced a local-first
  CLI that scrapes the web and scores leads using your own LLM keys. Free
  forever. 30s web demo if you want to play with the scoring engine:
  huntova.com/try"*

## Cold-email reply workflow (Gemini round 74)

By Tuesday morning, Monday's 50 emails will have produced 5–10 replies.

| Reply timing | What to do |
|---|---|
| **Monday replies** | Reply IMMEDIATELY. These are early believers. Point them straight at `/try`, then say "the full CLI is free on GitHub here: [link]". |
| **Tuesday replies during HN launch** | Batch for **18:00 ET**. Don't take eyes off HN for customer support. When you reply Tuesday evening, add HN social proof: *"Glad you liked it! We launched on Hacker News today and hit the front page [link] if you want to see the technical deep dive."* |

**The "Where did you get my email?" play (the holy grail):**

> I used the open-source CLI I just emailed you about. I saved a recipe with
> `huntova recipe save uk-marketing --queries "B2B SaaS marketing agency UK"`
> and ran it with `huntova recipe run uk-marketing`. It found your agency,
> extracted your name from the About page, and guessed your email format.
> You can do the exact same thing to your clients.

**Take a screenshot of this exchange (blur their name/email) and quote-tweet
it instantly.** It's the ultimate proof-of-work.

**Buyer-intent reply (the Round-68 Cloud Proxy wedge):**

> Right now, it's a 100% free local CLI. I'm building a 'Huntova Cloud'
> hosted tier next month that manages IP-rotation and runs hunts in the
> background so you don't get rate-limited by Google. Want me to ping you
> when the beta is live?

This captures 1–2 buyer leads for the future €29/mo paid tier.

## Failure-mode posture (GPT round 74)

If Tuesday underperforms, **Wednesday = iterate the product, soft relaunch**:

1. Pull top 20 HN comments + top 20 bounce points + install-failure reasons.
2. Decide whether the problem was **trust**, **install friction**, or
   **"why bother locally?"**.
3. Ship ONE obvious product change in 48–72h.
4. Relaunch through narrower communities (Plan B above).

> Treat launch day as a diagnostic, not a verdict.

The wrong move is to declare the idea dead after one HN cycle. The right
move is one HN cycle = one experiment.

## Long-game positioning (Gemini round 74)

Locked: **"The open-source lead engine"** — platform / protocol play, not
"Apollo for hackers" (dead-end — hackers don't buy enterprise sales tools)
and not "Buy your AI agent for sales" (red ocean — Artisan, 11x, Lavender
will outspend on UI/UX).

**Month-3 vision:**
- Agencies write custom `huntova-hubspot-custom-fields` plugins in Python.
- Founders share Recipes on GitHub like Dockerfiles: *"Here's my recipe for
  finding Dental Clinics using Wappalyzer + Google Maps."*
- The community builds the scraping heuristics + plugins (the moat).
- Monetisation: Huntova Cloud Proxy (rate-limit bypass) + private
  Plugin/Recipe Registry for enterprise teams.

> Build the Linux of lead generation. Let users build the UI on top.

## The dream outcome (Gemini round 75)

The **one big win** that fundamentally changes Huntova's trajectory if it
lands: **3 PRs submitted by community plugin authors within 48h.**

Not 1K stars (vanity). Not $200 from an agency owner (rounding error). Not
TechCrunch (fleeting). Three strangers reading the HN post, looking at the
codebase, understanding the `huntova-*` plugin architecture, and spending
their Tuesday night writing a `huntova-hubspot-sync` or
`huntova-github-commit-scraper` plugin to submit as a Pull Request.

That's the proof the architecture is extensible, the problem is universal,
and the community will build the moat. **A CLI tool with users is a
project; a CLI tool with external plugin contributors is an ecosystem.**

If it happens, **quote-tweet the PRs instantly** — that becomes the entire
Month-2 narrative. The "Linux of lead-gen" positioning either lands
organically with this signal or doesn't land at all.

## Pre-staged Tuesday-evening levers

The architecture below is **scaffolded pre-launch** but **gated behind
`HV_RECIPE_URL_BETA=1`**. Tuesday afternoon at ~16:00 ET, Enzo decides
based on HN sentiment whether to flip the flag and post a v1.1 follow-up.

- **Recipe URL import** (`/r/<slug>` + `huntova recipe publish` + `huntova
  recipe import-url`). 4–6 hours of code, ~2 hours to deploy.
  - If HN comments include "how do I share recipes?" → flip flag, post:
    *"Quick v1.1: recipe URLs are live. `huntova recipe publish agencies`
    → shareable link."*
  - If HN crashes hard → leave flag off (same-day v1.1 = desperate).
  - If HN goes phenomenal → leave flag off (users want stability not new
    features Tuesday).
  - Pre-launch tested via `pytest tests/test_recipe_urls.py` (commit 81
    — 11 tests covering gating, roundtrip, XSS, CSP, validation).
  - Defence-in-depth: HTML-escape every user-controlled field
    (commit 78), CSP header on /r/<slug> (commit 80).

## The buyer-intent reply (Round 76 synthesis)

Tuesday night Enzo sees ≥1 cold-email reply from an agency owner saying "this
is sick — when can I use it for my own client list?". Huntova engineering + Gemini
converged on a hybrid — give them the free CLI to prove the value AND open
the design-partner door for the Cloud Proxy wedge. Don't anchor a price yet
(GPT round 76 — premature precision before knowing operating cost), but DO
educate on the technical pain (Gemini round 76 — agency owners hit CAPTCHAs
at scale).

**Send pattern (~3 paragraphs):**

> Glad you liked it! You can actually use Huntova for your clients today —
> the core CLI is 100% free and open-source. If you have a developer on
> your team, just have them run `pipx install huntova` and plug in your
> Anthropic or Gemini API keys.
>
> One heads-up: running heavy scrapes locally means your IP will eventually
> get rate-limited by Google. I'm prototyping "Huntova Cloud Search" — a
> reliable hosted search endpoint that handles IP rotation in the
> background, so you keep the local CLI flow without running your own
> SearXNG.
>
> I'm looking for 3–5 design partners. Free for the first 90 days in
> exchange for usage feedback. Want me to put you on that list?

**Why this shape:** GPT's design-partner frame (no €29 commitment to a
product that doesn't exist), with Gemini's CAPTCHA pain education baked in
so the agency owner SEES why the cloud tier exists.

## Cloud Proxy MVP (GPT round 76)

The smallest credible paid wedge that captures Tuesday-night buyer intent:

**Pick: managed Huntova Cloud Search endpoint.**

- Hosted SearXNG-compatible proxy with sane uptime + rate limits + abuse
  protection.
- Users drop it into `HV_SEARXNG_URL` — same shape as a self-hosted SearXNG.
- Single paid plan, one endpoint, per-user token, usage cap / fair-use.
- Docs: `HV_SEARXNG_URL=https://cloud.huntova.com/...`
- No Slack/email uptime notices at launch.
- Ship in **week 3–4**.

**Why not the other options:**
- NOT hosted full agent: drags toward Apollo-in-a-browser, explodes auth +
  billing + trust scope.
- NOT background scheduler first: only matters after users trust the core.
- NOT premium recipe registry: monetises polish before pain.

**Pricing language (post-launch):** stay design-partner / free-90-days
through Month 1. Set price after observing operating cost from the first
3–5 partners. GPT round 76: "premature pricing creates fake precision
before Enzo knows the cost, usage profile, or support burden."

## Quote-tweet protocol (Gemini round 76)

When does an inbound reply become public content?

**Quote-tweet ONLY IF the reply demonstrates ONE of:**
1. Disbelief at the targeting accuracy (proof of product capability)
2. Shock at the cost savings (proof of pricing wedge)
3. Validation from a direct competitor's user (proof of switch-over)

**NEVER quote-tweet IF:**
- The reply contains a calendar link / "let's chat next week"
- The reply asks for a custom enterprise feature (private business)
- The reply tries to negotiate (private commercial conversation)

**The permission ask** (drop at the end of the natural reply, never as a
standalone DM):

> By the way, your reaction to this was awesome. Mind if I screenshot this
> exchange for Twitter? I will completely blur your name, email, and
> agency.

## Post-launch X strategy (Gemini round 76)

**"Shipment Friday" cadence.** NOT daily build-in-public posts (high time
cost, low compounding value). NOT a newsletter conversion (loses X
velocity).

- **Mon–Thu:** code, merge PRs, ignore the X feed. Only reply to direct
  @mentions about bugs.
- **Friday 10:00 ET:** drop ONE high-density thread. Format:
  > Huntova Week N Update.
  > 3 new community plugins merged. 1 major scraping bug fixed.
  > Here's a 30s video of the new HubSpot sync in action.

This trains the audience to expect high-signal release notes (not
thought-leadership), protects coding time, and reinforces the "Linux of
lead-gen" positioning. Linux maintainers ship release notes, not 10-tweet
inspiration threads.

## Day-1 dashboard (Kimi round 76)

Enzo runs a 30-second SQL-refresh loop in one SSH window plus the Railway
dashboard in a browser tab. **Seven metrics. Specific thresholds. Kill
switches pre-written.**

| # | Metric | Green | Yellow | Red | Action on red |
|---|---|---|---|---|---|
| 1 | `/try` success rate | >95% | 90–95% | <90% | Set `HV_KILL_TRY=1`, serve static "preview paused, install locally" page |
| 2 | `/api/try` p99 latency | <35s | 35–60s | >60s | Reduce `--max-leads` to 2 in the prompt; cap at 45s |
| 3 | `HV_DEMO_AI_KEY` budget left | >$15 | $5–$15 | <$5 | Disable `/try` until budget refilled |
| 4 | `try_submit` → `cli_init` conversion | >8% | 3–8% | <3% | README/landing CTA is broken; check `/download` → `/try` flow |
| 5 | `cli_init` → `cli_hunt` activation | >40% | 15–40% | <15% | `huntova doctor --quick` is failing; investigate |
| 6 | Railway 5xx rate | <0.1% | 0.1–1% | >1% | Rollback to last green commit |
| 7 | GitHub star velocity (first 6h) | >30/hr | 10–30/hr | <10/hr | HN post is flat; pivot to IH + X threads |

**The kill switch** (paste this into server.py for Tuesday morning):

```python
KILL_SWITCH_TRY = os.environ.get("HV_KILL_TRY", "0")

@app.post("/api/try")
async def api_try(...):
    if KILL_SWITCH_TRY == "1":
        return HTMLResponse(
            "<h1>Huntova preview is temporarily paused.</h1>"
            "<p>Install the CLI for full access:</p>"
            "<pre>pipx install huntova</pre>",
            status_code=503,
        )
    ...
```

Set `HV_KILL_TRY=1` in Railway env if metric 1 or 3 hits red. 10-second
deploy.

## The Wednesday retro (Kimi round 76)

**Format: 6 segments × 20 min. Data first, decisions second.** Pre-req
Monday night: set up `~/launch-retro/` directory, install `sqlite3` CLI.

### Segment 1 — Data collection (0:00–0:20)

```bash
# 1. Telemetry snapshot
sqlite3 metrics.sqlite <<'SQL'
SELECT event, COUNT(*) AS n, MAX(ts) AS latest
FROM metrics
WHERE ts > datetime('now', '-48 hours')
GROUP BY event ORDER BY n DESC;
SQL

# 2. /try funnel
sqlite3 metrics.sqlite <<'SQL'
SELECT
  COUNT(DISTINCT CASE WHEN event='try_submit' THEN 1 END) AS tries,
  COUNT(DISTINCT CASE WHEN event='cli_init'   THEN 1 END) AS inits,
  COUNT(DISTINCT CASE WHEN event='cli_hunt'   THEN 1 END) AS hunts
FROM metrics WHERE ts > datetime('now', '-48 hours');
SQL

# 3. Error tail (last 50 unique)
journalctl -u huntova -n 500 \
  | grep -iE "(error|exception|traceback)" \
  | tail -50 | sort | uniq -c | sort -rn | head -20

# 4. HN comment scrape — paste the HN thread into ~/launch-retro/hn.txt
grep -ciE "(bug|crash|broken|error|fail|doesn't work)" hn.txt
grep -ciE "(love|great|awesome|perfect|exactly|need this)" hn.txt
grep -ciE "(how|question|confused|unclear|what|why)" hn.txt
```

### Segment 2 — Bug triage (0:20–0:40)

For each of the top 20 errors, ask:
- Did it prevent a hunt from completing? → P0 if yes
- Did it happen on a clean machine? → higher priority if yes
- Can I reproduce in 10 min? → "needs info" if no

Output: top 5 bugs with `Frequency / Severity / Fix Time / Action`.

### Segment 3 — Objection extraction (0:40–1:00)

Tag each HN comment with one of: `PRICING`, `SETUP`, `FEATURE`,
`COMPETITOR`, `TRUST`. Output: top 5 objections with `Count / Type /
Response` (FAQ entry, README clarification, or product change).

### Segment 4 — Love signals (1:00–1:20)

Search `hn.txt` and X for quoted praise. Output: top 5 with
`Quote / Source / Amplification` (screenshot+quote-tweet, landing page
testimonial, or thank-you DM).

### Segment 5 — Three go/no-go decisions (1:20–1:40)

| Decision | Go IF | No-go IF |
|---|---|---|
| Flip `HV_RECIPE_URL_BETA` | ≥2 HN comments ask "how do I share this?" OR ≥5 recipe-related tweets | Zero organic mentions of sharing |
| Indie Hackers post Wednesday | HN >150 points but conversation dying (last comment >3h ago) | HN still active OR went negative (<50 points) |
| Cut Cloud Proxy MVP | ≥3 comments say "I won't run this locally" or "I want SaaS" | Local-first positioning resonated |

### Segment 6 — Week 2 roadmap (1:40–2:00)

≤5 items, ordered by impact/effort. Hot-fixes first, polish last.

## Pre-Tuesday hot-fix candidate (Kimi round 76)

**`huntova hunt --explain-scores`** — the most embarrassing Day-1 gap.

The README claims "AI-powered 5-dimensional scoring" but `huntova hunt`
currently prints opaque float scores. A skeptical HN comment will ask
"how do I know the AI isn't hallucinating these scores?" Without an
answer, trust collapses.

Kimi recommended this as a Wednesday hot-fix. **It should ship before
Tuesday — 30-min change, foundational.** Sample output once flag is on:

```
Strobe Media       8.4 (fit 7.2 + ev 8.0 + reach 9.0 + adapt +1.2)
KONZEPT.           7.9 (fit 6.5 + ev 7.5 + reach 8.5)
```

## Post-launch architecture roadmap (Kimi round 75)

If launch goes well (≥200 stars / ≥30 installs):

| Week | Ship | Why |
|---|---|---|
| 2 | Flip recipe URLs + `huntova metrics` CLI | Recipe URLs are built — flip the flag. Metrics CLI lets Enzo query the server DB from his laptop. |
| 3 | `huntova outreach send` | Closes the loop: find → score → save → **send**. Turns Huntova from a research tool into a revenue tool. |
| 4 | /h/<slug> view tracker | "23 people viewed your shared hunt" — engagement signal + retention hook. |

If launch goes poorly (<100 stars / <10 installs):

| Week | Ship | Why |
|---|---|---|
| 2 | Bug bash + expand `huntova doctor` | If people tried it and bounced, the failure is in the first 2 minutes. Add probes + better error messages. |
| 3 | `huntova outreach send` | Same as above — but now the "this is why you should still care" feature. Complete workflow > research-only. |
| 4 | Plugin discovery improvements | If installs are low but plugins are zero, the ecosystem isn't bootstrapping. Better `plugins search`, add `plugins install --all`. |

**Skipped indefinitely:** plugin sandboxing (no incident, capability flags
+ CSP suffice), workflow YAML (recipes + plugins + URLs cover it),
multi-agent orchestration (`asyncio.gather` on hooks is parallel enough),
`huntova examples reproduce` (gimmicky — open-source code IS the
reproducibility), `huntova try-locally` (don't blur the preview boundary).

## Enzo's 30-day calendar (GPT round 76)

Meta-rule: **NO MORE THAN TWO STRATEGIC FRONTS AT ONCE.** For the next
30 days, those fronts are:

1. Make the current product more trustworthy from first install to
   second hunt.
2. Turn buyer intent into a tiny paid Cloud Proxy wedge.

Everything else supports one of those two or waits.

| Bucket | % | Justification |
|---|---|---|
| Bug-fix + hardening | 30% | Launch feedback surfaces trust killers fast. First-month focus is tightening the install path, hunt quality, and obvious rough edges before anything else. |
| Cloud Proxy MVP | 25% | Clearest revenue wedge + smallest plausible paid offer. Bounded chunk — don't let it eat the whole month. |
| Community plugins / ecosystem | 15% | Plugin surface is a real moat. Focus on enabling 2–3 serious plugin authors, not running a "community program". |
| `huntova outreach send` / product expansion | 10% | Important second-order loop. Don't dilute focus before the install path is stable. |
| Marketing / content / interviews | 10% | Keep momentum with one strong artifact / week. Founder-led content works batched, not constant. |
| Human buffer (day job, family, sleep) | 10% | NON-NEGOTIABLE. The goal is not burning out by Day 14. |
