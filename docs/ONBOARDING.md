# First-run onboarding

After installing Huntova (`docs/INSTALL.md`), run the onboarding
wizard to pick an AI provider, save a key, and launch the dashboard:

```bash
huntova onboard
```

The wizard is non-destructive — you can re-run it any time to switch
providers or update a key. Existing keys are detected and reused
unless you explicitly say otherwise.

## What the wizard does

**Step 1 — Filesystem.** Verifies the config and data directories
exist and are writable, and reports the secrets backend in use
(`keyring` on most systems, an encrypted file fallback on Linux
without a keyring service).

**Step 2 — Provider.** Probes localhost for running local AI servers
(Ollama, LM Studio, llamafile) and lists detected ones FIRST so
budget-conscious users see the free options up front. Cloud
providers come next: Gemini, Anthropic, OpenAI, OpenRouter, Groq,
DeepSeek, Together, Mistral, Perplexity. Custom OpenAI-compatible
endpoints sit at the bottom for advanced users.

You select one, paste the key (input hidden via `getpass`), and
Huntova runs a 5-token live probe to confirm the key works.

**Step 3 — Launch.** Suggests the 60-second cold-email pipeline —
`huntova examples install <recipe> && huntova recipe run <recipe> &&
huntova outreach send --top 5 --dry-run` — then offers to open the
dashboard at `http://127.0.0.1:5050/`.

## Skipping keys initially

You don't have to enter a key to finish setup. Press Ctrl+C at the
key prompt, or pick a local provider you haven't started yet. The
wizard records your provider preference in `~/.config/huntova/
config.toml` (`preferred_provider = "ollama"`) and exits cleanly.
You can paste the key later via:

```bash
huntova onboard --force         # re-runs with the existing config preserved
# OR
export HV_GEMINI_KEY=…
huntova hunt                    # the env var is read at hunt time
```

## Local AI providers

If you have **Ollama**, **LM Studio**, or **llamafile** running on
localhost, the wizard auto-detects them and skips the API-key step.
There's no auth required by default for these — the local server
binds to localhost and no traffic leaves your machine.

To install one:

```bash
# Ollama
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.2

# LM Studio
# Download from https://lmstudio.ai/ and start the local server
```

Then re-run `huntova onboard` and pick the detected entry. The
wizard shows the model count it found (`ollama (detected, 2 models
loaded)`).

## Custom OpenAI-compatible endpoints

If you have a LiteLLM gateway, a self-hosted vLLM instance, an
enterprise relay, or any other OpenAI-compatible endpoint, pick
"Custom" in the wizard. You'll be asked for:

- **Base URL** — the `https://your-endpoint/v1` your service exposes
- **Model name** — what to pass as the `model` parameter
- **API key** — optional if your endpoint is open

The wizard saves all three and runs the same live probe.

## Browser-only mode (no terminal interaction)

If you'd rather configure Huntova through the browser:

```bash
huntova onboard --browser
```

This skips the CLI prompts, starts `huntova serve` in the background,
and opens `http://127.0.0.1:5050/setup` — a 3-tab web wizard
(Cloud APIs / Local AI / Custom endpoint) with the same provider
catalogue and the same OS-keychain saving.

## Re-running onboarding

`huntova onboard` is idempotent. On a second run:

- It re-uses the existing `config.toml`.
- For each provider with a saved key, it shows a "use existing key?"
  prompt before re-collecting.
- It detects the running daemon (if any) and offers to restart it
  with the new provider.

You can also use `huntova onboard --force` to clear the cache and
prompt for everything fresh.

## CI / scripted setup (no terminal)

```bash
huntova onboard --no-prompt --no-launch
```

In `--no-prompt` mode, the wizard prints filesystem state, lists the
provider env-var names you need to set, and exits 0 without prompting.
Pair with explicit env vars in your CI:

```bash
export HV_GEMINI_KEY=$GEMINI_KEY_FROM_VAULT
huntova onboard --no-prompt --no-launch
huntova hunt --max-leads 5 --json
```

## After onboarding: where to go next

- **Run a hunt:** `huntova examples ls` lists 4 starter recipes;
  `huntova recipe run tech-recruiting` (or any other example)
  produces ~25 qualified leads in a few minutes.
- **Send outreach:** `huntova outreach send --top 5 --dry-run`
  previews the AI-drafted emails; re-run without `--dry-run` to
  deliver via your SMTP.
- **Open the dashboard:** `huntova serve` opens
  `http://127.0.0.1:5050/` for the visual CRM view.
- **Daemonise:** `huntova daemon install` keeps `huntova serve`
  running across reboots so your dashboard URL is always available.

## Troubleshooting onboarding

**"keyring not available"** — common on headless Linux. Huntova
falls back to an encrypted file at
`~/.config/huntova/secrets.enc` automatically. The wizard prints
which backend it's using under "Step 1 — Filesystem" so you'll see
it.

**"AI probe failed"** — the key was saved but the live test couldn't
reach the provider. Common causes: corporate firewall blocking the
provider's API, key copied with trailing whitespace (the wizard
strips it but check anyway), or you picked a provider whose default
model name isn't available on your account (e.g. you don't have
access to Claude Sonnet 4.5 yet — switch via `HV_ANTHROPIC_MODEL`
env var).

See `docs/TROUBLESHOOTING.md` for the full list.
