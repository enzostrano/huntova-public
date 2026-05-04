# Troubleshooting

## Quick diagnosis

Run these in order. The first one that fails points at the issue:

```bash
huntova version              # binary on PATH? prints e.g. 0.1.0a1
huntova status               # one-screen operational dashboard
huntova doctor               # full diagnostic — non-zero exit on critical failures
huntova test-integrations    # live probes every configured integration
```

`huntova doctor` is the canonical command. It checks: providers
configured, SearXNG reachable, data dir writable, Playwright + Chromium
installed, plugin discovery clean, AI key actually valid.

## Installation issues

### `huntova: command not found`

`pipx`'s shim directory isn't on your PATH. Run:

```bash
pipx ensurepath
exec $SHELL
```

Then re-run `huntova version`.

### `Python 3.x detected. Huntova requires Python 3.11 or newer.`

Install a newer Python via your package manager:

- **macOS:** `brew install python@3.13`
- **Ubuntu / Debian:** `sudo apt install python3.13` (or use deadsnakes PPA)
- **Windows:** `winget install Python.Python.3.13`

Then re-run the installer with the newer Python explicitly:

```bash
python3.13 -m pip install --user pipx
python3.13 -m pipx ensurepath
pipx install huntova --python python3.13
```

### `pip install huntova` fails with `Package 'huntova' requires a different Python`

You're using a Python older than 3.11. See above — install a current Python.

### Playwright Chromium download stuck or fails

Skip it for now — Huntova falls back to a `requests`-only mode when
Playwright isn't available. Lead quality drops slightly (no deep
qualification of contact pages) but everything else works:

```bash
# Skip Playwright entirely:
huntova hunt --max-leads 5

# Install it later when you have time:
pipx inject huntova playwright
playwright install chromium
```

## Configuration issues

### `no API key configured. Run `huntova init` first or set HV_GEMINI_KEY`

You haven't configured a provider yet. Run:

```bash
huntova onboard
```

The wizard saves your key to the OS keychain. Or set an env var:

```bash
export HV_GEMINI_KEY=…
huntova hunt
```

### `keyring: backend not found` (Linux)

Common on headless Linux without a desktop environment. Huntova
falls back to an encrypted file at `~/.config/huntova/secrets.enc`
automatically — `huntova doctor` will print `secrets backend:
fernet-encrypted-file` instead of `keyring`. No action needed.

If you'd rather use a real keyring, install one:

```bash
# Ubuntu / Debian
sudo apt install gnome-keyring
# or for a headless server, secret-tool:
sudo apt install libsecret-tools
```

### `huntova config show` shows a key as `***redacted***`

By design — `huntova config show` redacts anything matching `*key*`,
`*token*`, `*secret*`, or `*password*`. To verify the actual saved
value, use the keychain directly:

```bash
# macOS:
security find-generic-password -a huntova -s HV_GEMINI_KEY -w
# Linux (libsecret):
secret-tool lookup huntova HV_GEMINI_KEY
```

### `os.environ.HV_GEMINI_KEY` is set but Huntova doesn't pick it up

Check the resolution order: keychain wins over env. If you've saved
a different key to the keychain previously, that one wins. Force a
re-save with:

```bash
huntova onboard --force
```

## Hunt issues

### `[huntova] hunt completed: 0 leads`

Three common causes:

1. **SearXNG unreachable.** Run `huntova test-integrations` — the
   SearXNG check tells you whether it's reachable. If unreachable:
   ```bash
   docker run -d --name=searxng -p 8888:8080 searxng/searxng
   export SEARXNG_URL=http://localhost:8888
   ```

2. **Public SearXNG with JSON API disabled.** Most public instances
   block JSON to prevent abuse. Self-host one (above) or pay for the
   Huntova Cloud Search proxy.

3. **Query too narrow.** The agent's default queries cover broad
   geographies; if you're hunting in a niche industry the SERPs may
   have nothing. Try one of the bundled examples:
   ```bash
   huntova examples install tech-recruiting
   huntova recipe run tech-recruiting
   ```

### `playwright._impl._errors.TimeoutError` mid-hunt

The qualification step couldn't reach a candidate's website. The
agent automatically falls back to `requests`-only mode for that
lead and continues. Not a fatal error — appears in `--verbose` logs
but doesn't stop the hunt.

If it happens for **every** lead, your machine probably can't reach
the open web from Playwright (proxy issue, container without
network). Add the proxy:

```bash
export HTTP_PROXY=…
export HTTPS_PROXY=…
huntova hunt
```

### `RuntimeError: anthropic SDK not installed`

You picked Anthropic but didn't install the optional dependency:

```bash
pipx inject huntova anthropic
# or
pip install --user huntova[anthropic]
```

## Outreach issues

### `huntova outreach send` says `SMTP not configured`

Set the SMTP env vars before running:

```bash
export SMTP_HOST=smtp.gmail.com
export SMTP_PORT=587
export SMTP_USER=you@yourdomain.com
export SMTP_PASSWORD=…       # use an app password, not your main password
huntova outreach send --top 5 --dry-run
```

For Gmail specifically, generate an app password at
`https://myaccount.google.com/apppasswords` (requires 2FA).

### Emails go to spam

Three things to check:

- **SPF / DKIM / DMARC** on your sending domain. If you're sending
  from Gmail/Workspace it's set up automatically; from a custom
  domain you'll need to configure DNS.
- **Subject line.** Round 73's GTM brainstorm flagged "scraper" +
  "leads" + a personalisation token as a near-perfect spam-filter
  match. The bundled subject is `Open-source alternative to Apollo
  for [Their Agency]` — see `docs/LAUNCH.md` for the locked subject.
- **Send velocity.** Stagger 50 sends in batches of 10 every
  10 minutes — Google Workspace flags single-domain bursts.

### `Email rewrite failed (no JSON)`

The agent's email-drafting LLM returned non-JSON. Re-run with
`--explain-scores` to see which leads' scoring went sideways.
If it persists, switch provider with:

```bash
huntova onboard --force      # pick a different provider
```

## Daemon issues

### `huntova daemon install` fails on macOS with `launchctl: bootstrap …`

Older macOS versions used different `launchctl` syntax. The
installer falls back to `launchctl load -w` which is portable. If
that also fails:

```bash
# Manually load:
launchctl load -w ~/Library/LaunchAgents/com.huntova.gateway.plist
# Confirm it's loaded:
launchctl list com.huntova.gateway
```

### `huntova daemon install` fails on Linux with `Failed to start`

Check the systemd journal:

```bash
journalctl --user -u huntova.service -n 50
```

Common causes: SECRET_KEY env var missing on a system service (use
`huntova daemon install` which inherits your shell env automatically),
or the configured port is already bound.

### Daemon runs but `huntova status` says `server stopped`

Status probes `127.0.0.1:5050/api/runtime` with a 0.5s timeout. If
the daemon is bound but slow to respond on first request, run
`huntova status` again — the timeout is intentionally tight to keep
the dashboard snappy.

## Web wizard / dashboard issues

### `/setup` shows "✓ saved" badges for providers I never configured

This was a real bug in earlier builds where test POSTs left fake
keys in the keychain. Clean them with:

```bash
# macOS:
security delete-generic-password -a huntova -s HV_GEMINI_KEY
# Linux:
secret-tool clear huntova HV_GEMINI_KEY
```

Or remove all Huntova keychain entries:

```bash
# macOS:
security delete-generic-password -s huntova
```

### `/api/setup/key` returns 403 CSRF validation failed

The endpoint is on the CSRF exempt list, but if you're testing via
curl from a different host, the local-mode guard kicks in:

```bash
APP_MODE=local huntova serve --port 5050
# now POST works from localhost
```

In production (`APP_MODE=cloud`), `/api/setup/key` returns 403 by
design — keys belong on the CLI machine, not on the cloud server.

## Update / uninstall issues

### `pipx upgrade huntova` says "huntova is already at the latest version"

That's correct — pipx checks PyPI. If you need to force a re-install
(e.g. after editing source):

```bash
pipx install --force huntova
```

### Lost data after `pipx uninstall huntova`

`pipx uninstall` only removes the binary. Your data is intact at:

```bash
~/.local/share/huntova/db.sqlite       # leads + history
~/.config/huntova/                     # config + plugins + secrets fallback
```

Re-install and your previous leads will reappear in
`huntova ls`. To intentionally wipe data:

```bash
rm -rf ~/.local/share/huntova ~/.config/huntova
```

## Still stuck?

Open an issue at https://github.com/enzostrano/huntova-public/issues
with the output of:

```bash
huntova doctor 2>&1
huntova test-integrations 2>&1
```

Both commands redact secrets automatically.
