# Installing Huntova

Huntova is a local-first command-line lead-generation agent. It runs
on your machine, stores leads in a SQLite file under your home
directory, and calls the AI provider you configure with **your own
API key** — no Huntova-hosted billing, no central database.

This guide covers installation on macOS, Linux, and Windows. See
`docs/ONBOARDING.md` for the first-run setup wizard, and
`docs/CONFIG.md` for how settings are stored.

## System requirements

- **Python 3.11 or newer.** Huntova uses `pipx` for isolated
  installation, so it doesn't pollute your system Python or fight
  with your other tools.
- **macOS 13+** / **Ubuntu 22.04+** (or any current desktop Linux) /
  **Windows 11** (PowerShell or WSL).
- Roughly **400 MB** of disk space (the wheel + dependencies + the
  optional Playwright Chromium browser used for deep-qualifying
  prospects).

## Recommended: one-line installer (macOS / Linux / WSL)

```bash
curl -fsSL https://github.com/enzostrano/huntova-public/releases/latest/download/install.sh | sh
```

The script:
1. Detects your Python toolchain and refuses to continue on Python <3.11.
2. Installs `pipx` if missing (via Homebrew on macOS, `apt-get` on
   Ubuntu, or `pip --user` everywhere else).
3. Runs `pipx install huntova` (or `pipx upgrade huntova` if you're
   already on it).
4. Optionally installs the Playwright Chromium browser used for
   higher-quality lead qualification.
5. Offers to launch `huntova onboard` immediately to configure a
   provider.

Read the script before piping it to `sh` if that's your preference —
you'll find it at `static/install.sh` in the repo, served by the
running app at `/install.sh` in production.

## Alternative: pipx directly

If you already have `pipx` and want to skip the helper script:

```bash
pipx install huntova
huntova onboard
```

`pipx` keeps Huntova in its own virtualenv at `~/.local/pipx/venvs/`
and exposes the `huntova` binary on your PATH.

## Alternative: pip with --user (no pipx)

```bash
python3 -m pip install --user huntova
huntova onboard
```

This installs into `~/.local/lib/python3.x/site-packages/`. Less
isolated than `pipx`; if your global Python ever changes major
versions, you'll re-install. Use `pipx` if you can.

## Alternative: from source

```bash
git clone https://github.com/enzostrano/huntova-public.git
cd huntova-public
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
huntova onboard
```

This is the development path. Edits to the source are picked up
without re-installing (the `-e` flag).

## Windows (PowerShell)

We don't ship a one-line PowerShell installer yet. The reliable path
is:

```powershell
# 1) install Python 3.11+ from python.org or via winget:
winget install Python.Python.3.13
# 2) install pipx
python -m pip install --user pipx
python -m pipx ensurepath
# (close + reopen PowerShell so the PATH change takes effect)
# 3) install Huntova
pipx install huntova
huntova onboard
```

If you'd rather use WSL, install Ubuntu 22.04+ from the Microsoft
Store and follow the macOS/Linux instructions inside it. The CLI
runs identically.

## After install: verify

Once installed, run any of these to confirm the binary is on your
PATH:

```bash
huntova version          # prints the version string
huntova doctor           # diagnostic — runs every health check
huntova status           # operational dashboard
huntova test-integrations  # probes each configured AI provider, SearXNG, Playwright, plugins
```

If `huntova` isn't found, your shell PATH may not include the pipx
shim directory. Run:

```bash
pipx ensurepath          # writes the PATH update to your shell config
exec $SHELL              # reload the shell
```

## Updating

```bash
pipx upgrade huntova                     # if you used pipx
python3 -m pip install --upgrade --user huntova  # if you used pip --user
```

Or just re-run the one-line installer above — it detects an existing
install and upgrades in place.

## Uninstalling

```bash
pipx uninstall huntova
```

To also remove your data and config:

```bash
rm -rf ~/.local/share/huntova ~/.config/huntova
# macOS: also remove the keychain entries
security delete-generic-password -s huntova  # one entry per provider key
```

That's the complete state. Huntova never writes outside those two
directories on macOS / Linux. On Windows, replace
`~/.local/share/huntova` with `%LOCALAPPDATA%\huntova` and
`~/.config/huntova` with `%APPDATA%\huntova`.

## Troubleshooting installation

See `docs/TROUBLESHOOTING.md` for common installer failures (Python
too old, pipx not on PATH, Playwright fails to download, keychain
unavailable on headless Linux).
