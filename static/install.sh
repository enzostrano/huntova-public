#!/usr/bin/env bash
# Huntova one-shot installer.
# Usage: curl -fsSL https://github.com/enzostrano/huntova-public/releases/latest/download/install.sh | sh
#
# Detects Python, bootstraps pipx, installs Huntova from the public
# repo, optionally installs Playwright Chromium, then launches the
# onboarding wizard. Idempotent — safe to re-run for upgrades.

set -e
# pipefail so e.g. `pipx run … | grep` returns the real exit code,
# not grep's. Otherwise a Playwright crash hidden behind a grep filter
# would falsely report success.
set -o pipefail
# Best-effort UTF-8 — the ASCII logo + 🦊 emoji + box-drawing chars
# render as garbage on bare LANG=C terminals (cloud-init shells, ssh
# from Windows ConEmu without UTF-8). No-op when locale is already set.
export LC_ALL="${LC_ALL:-C.UTF-8}" LANG="${LANG:-C.UTF-8}" 2>/dev/null || true

# ── Pretty output ─────────────────────────────────────────────────
ESC=$'\033'
RESET="${ESC}[0m"; BOLD="${ESC}[1m"; DIM="${ESC}[2m"
GREEN="${ESC}[32m"; RED="${ESC}[31m"; YELLOW="${ESC}[33m"
PURPLE="${ESC}[35m"; CYAN="${ESC}[36m"; PINK="${ESC}[38;5;213m"

step() { printf '%s▸%s %s\n' "$CYAN" "$RESET" "$1"; }
ok()   { printf '%s✓%s %s\n' "$GREEN" "$RESET" "$1"; }
warn() { printf '%s!%s %s\n' "$YELLOW" "$RESET" "$1"; }
die()  { printf '%s✗%s %s\n' "$RED" "$RESET" "$1" >&2; exit 1; }
chat() { printf '  %s🦊 huntova:%s %s%s%s\n' "$PINK" "$RESET" "$DIM" "$1" "$RESET"; }

# ── ASCII logo ────────────────────────────────────────────────────
echo
printf '%s' "$PURPLE"
cat <<'LOGO'
   ██   ██ ██    ██ ███    ██ ████████  ██████  ██    ██  █████
   ██   ██ ██    ██ ████   ██    ██    ██    ██ ██    ██ ██   ██
   ███████ ██    ██ ██ ██  ██    ██    ██    ██ ██    ██ ███████
   ██   ██ ██    ██ ██  ██ ██    ██    ██    ██  ██  ██  ██   ██
   ██   ██  ██████  ██   ████    ██     ██████    ████   ██   ██

LOGO
printf '%s' "$RESET"
printf "        %slocal-first BYOK lead-gen agent · find clients while you sleep%s\n" "$DIM" "$RESET"
echo
chat "alright let's do this — installing me on your machine should take ~30s..."
echo

# ── Pre-flight: OS sanity ─────────────────────────────────────────
case "$(uname -s 2>/dev/null)" in
  MINGW*|MSYS*|CYGWIN*)
    chat "wait, you're on a bare Windows shell? not my scene. open PowerShell and:"
    die "  winget install Python.Python.3.13
       python -m pip install --user pipx
       python -m pipx ensurepath
       pipx install git+https://github.com/enzostrano/huntova-public.git
       Then restart PowerShell and run: huntova onboard"
    ;;
esac

# ── Pre-flight: Python ────────────────────────────────────────────
chat "step 1/4: looking for a python you haven't already broken..."
# `curl | sh` runs in a non-login non-interactive shell. On macOS
# `/opt/homebrew/bin` and `/usr/local/bin` aren't on PATH by default
# in that context — Homebrew adds them to ~/.zshrc but that file
# never loads here. Source brew env explicitly so `python3.13`
# becomes findable. Same for Linux Homebrew at /home/linuxbrew/...
for _bp in /opt/homebrew/bin/brew /usr/local/bin/brew /home/linuxbrew/.linuxbrew/bin/brew; do
  [ -x "$_bp" ] && eval "$("$_bp" shellenv)" 2>/dev/null && break
done

# Prefer explicit-version interpreters first (3.13 → 3.12 → 3.11).
# macOS ships python3 = 3.9.6 by default, but Homebrew users almost
# always also have python3.13 from `brew install python@3.13`. The
# original `command -v python3` picked /usr/bin/python3 and bailed
# with a confusing "install Python 3.13" message even when it was
# already installed.
#
# Also probe well-known absolute paths for cases where brew env
# couldn't be sourced (e.g. user installed via official python.org
# .pkg, or pyenv shim isn't on PATH yet).
_PROBE_PATHS="
/opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11
/usr/local/bin/python3.13 /usr/local/bin/python3.12 /usr/local/bin/python3.11
/usr/local/opt/python@3.13/bin/python3.13 /usr/local/opt/python@3.12/bin/python3.12 /usr/local/opt/python@3.11/bin/python3.11
/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13
/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12
/Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11
/home/linuxbrew/.linuxbrew/bin/python3.13 /home/linuxbrew/.linuxbrew/bin/python3.12 /home/linuxbrew/.linuxbrew/bin/python3.11
"

PY=""
_check_py() {
  cand="$1"
  cand_ver=$("$cand" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "0.0")
  cand_major=$(echo "$cand_ver" | cut -d. -f1)
  cand_minor=$(echo "$cand_ver" | cut -d. -f2)
  if [ "${cand_major:-0}" -ge 3 ] && [ "${cand_minor:-0}" -ge 11 ]; then
    return 0
  fi
  return 1
}

# First pass: PATH-resolved candidates.
for cand in python3.13 python3.12 python3.11 python3 python; do
  if command -v "$cand" >/dev/null 2>&1 && _check_py "$cand"; then
    PY="$cand"; break
  fi
done

# Second pass: well-known absolute paths (brew, official .pkg, pyenv).
if [ -z "$PY" ]; then
  for cand in $_PROBE_PATHS; do
    if [ -x "$cand" ] && _check_py "$cand"; then
      PY="$cand"; break
    fi
  done
fi

# Last-resort fallback so the error message names something concrete.
if [ -z "$PY" ]; then
  for cand in python3 python; do
    command -v "$cand" >/dev/null 2>&1 && PY="$cand" && break
  done
fi

if [ -z "$PY" ]; then
  chat "no python found. that's actually impressive in 2026."
  die "Python 3.11+ is required.
       macOS:    brew install python@3.13
       Ubuntu:   sudo apt install python3.13
       Windows:  winget install Python.Python.3.13"
fi

PY_VER=$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; }; then
  chat "python $PY_VER is older than my last hunt. need 3.11 minimum."
  die "Python $PY_VER detected. Huntova requires Python 3.11 or newer.
       macOS:    brew install python@3.13
       Ubuntu:   sudo apt install python3.13
       Windows:  winget install Python.Python.3.13"
fi
ok "Python ${PY_VER} (${BOLD}${PY}${RESET})"

# Upgrade pip if it's too old for pyproject.toml-only installs.
PIP_MAJOR=$("$PY" -m pip --version 2>/dev/null | awk '{print $2}' | cut -d. -f1 || echo 0)
PIP_MINOR=$("$PY" -m pip --version 2>/dev/null | awk '{print $2}' | cut -d. -f2 || echo 0)
if [ "${PIP_MAJOR:-0}" -lt 21 ] || { [ "${PIP_MAJOR:-0}" -eq 21 ] && [ "${PIP_MINOR:-0}" -lt 3 ]; }; then
  chat "pip is older than your favourite hoodie. let me upgrade it..."
  "$PY" -m pip install --user --quiet --upgrade pip 2>&1 | grep -v "Looking in" || true
fi

# ── pipx ──────────────────────────────────────────────────────────
chat "step 2/4: installing pipx so I don't pollute your global python..."
if ! command -v pipx >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    brew install pipx >/dev/null 2>&1 || die "brew install pipx failed"
  elif command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -qq >/dev/null 2>&1 || true
    sudo apt-get install -y pipx >/dev/null 2>&1 || \
      "$PY" -m pip install --user --quiet pipx || die "pip install pipx failed"
  else
    "$PY" -m pip install --user --quiet pipx || die "pip install pipx failed"
  fi
  "$PY" -m pipx ensurepath --quiet >/dev/null 2>&1 || true
  export PATH="$HOME/.local/bin:$PATH"
  ok "pipx installed"
else
  ok "pipx already installed"
fi

# ── Install / upgrade Huntova ─────────────────────────────────────
chat "step 3/4: pulling the agent. this is the bit where I learn to hunt..."
HUNTOVA_GIT_URL="git+https://github.com/enzostrano/huntova-public.git"
if pipx list --short 2>/dev/null | awk '{print $1}' | grep -qx huntova; then
  # `pipx upgrade` (without --force) silently no-ops when the
  # package is already at the latest published version, even when the
  # local venv is broken (missing deps, half-installed). Use --force
  # so re-running the installer always nukes + rebuilds the venv,
  # matching the install --force fallback below. Idempotent for
  # broken prior installs.
  pipx upgrade --force huntova >/dev/null 2>&1 || \
    pipx install --force "$HUNTOVA_GIT_URL" >/dev/null 2>&1 || \
    die "pipx upgrade huntova failed"
else
  pipx install "$HUNTOVA_GIT_URL" >/dev/null 2>&1 || \
    die "pipx install $HUNTOVA_GIT_URL failed"
fi
# Inject the polished-TUI dep so first-run users see the rich wizard.
pipx inject huntova questionary >/dev/null 2>&1 || true
ok "Huntova installed"

# PATH check — pipx may not be in current shell yet.
if ! command -v huntova >/dev/null 2>&1; then
  warn "huntova binary not yet on PATH — adding $HOME/.local/bin"
  export PATH="$HOME/.local/bin:$PATH"
  if ! command -v huntova >/dev/null 2>&1; then
    chat "the binary's installed but your shell hasn't noticed. open a new terminal and run: huntova onboard"
    die "PATH update incomplete. Try a new terminal or run:
       export PATH=\"\$HOME/.local/bin:\$PATH\"
       huntova onboard"
  fi
fi

# ── Optional: Playwright Chromium ─────────────────────────────────
if [ -t 0 ] && [ -t 1 ]; then
  echo
  chat "step 4/4: chromium for deep-qualify (optional, ~120MB). worth it for lead quality."
  printf "  %s?%s Install Playwright Chromium? [Y/n]: " "$CYAN" "$RESET"
  read -r ans
  ans="${ans:-y}"
  if [ "$ans" = "y" ] || [ "$ans" = "Y" ]; then
    chat "downloading chromium. this is the slowest part — try the kettle."
    pipx inject huntova playwright >/dev/null 2>&1 || true
    # No `| grep` here — set -o pipefail would mask Playwright's real
    # exit code if grep filtered the meaningful line out. Plain
    # if/else with stderr surfaced.
    if pipx run --spec huntova playwright install chromium >/dev/null 2>&1; then
      ok "Playwright ready"
    else
      warn "Playwright Chromium install hiccuped — agent will run in requests-only mode"
    fi
  else
    chat "fine, skip it. I'll run lighter without deep-qualify. you can pipx inject later."
  fi
fi

# ── Done ──────────────────────────────────────────────────────────
echo
printf '%s' "$PURPLE"
cat <<'DONE'
   ╭──────────────────────────────────────────────────────────╮
   │                                                          │
   │   ✓ Huntova is installed and ready to hunt.              │
   │                                                          │
   │   Next: huntova onboard                                  │
   │                                                          │
   │     · 60-second wizard                                   │
   │     · picks an AI provider (Claude is the default)       │
   │     · saves your key to the OS keychain                  │
   │     · opens the dashboard in your browser                │
   │                                                          │
   ╰──────────────────────────────────────────────────────────╯
DONE
printf '%s' "$RESET"
echo
chat "13 providers ready, default is Claude (we built me with Claude — credit where due)."
chat "type 'huntova chat' anytime to talk to me directly."
echo
printf "  Docs:    %s\n" "${CYAN}https://github.com/enzostrano/huntova-public${RESET}"
printf "  Issues:  %s\n" "${CYAN}https://github.com/enzostrano/huntova-public/issues${RESET}"
printf "  Support: %s\n" "${CYAN}hello@huntova.com${RESET}"
echo

# Auto-launch.
#
# Two paths:
# (1) Interactive TTY (`./install.sh` direct, or `bash <(curl ...)`)
#     → ask the user, then `exec huntova onboard` in-place.
# (2) Non-interactive (`curl ... | sh` — stdin is the pipe from curl,
#     not the terminal) → spawn `huntova serve` in the background and
#     open the browser to the web wizard at /setup. The user gets the
#     same experience without needing to retype anything.
if [ -t 0 ] && [ -t 1 ]; then
  printf "  %s?%s Run %shuntova onboard%s now? [Y/n]: " "$CYAN" "$RESET" "$BOLD" "$RESET"
  read -r run_ans
  run_ans="${run_ans:-y}"
  if [ "$run_ans" = "y" ] || [ "$run_ans" = "Y" ]; then
    chat "alright, taking it from here..."
    echo
    exec huntova onboard
  fi
else
  # curl | sh path — auto-launch the browser-based wizard.
  # NOTE: `set -e` + `set -o pipefail` in this block is dangerous
  # because lsof / kill / grep etc. routinely exit non-zero on
  # empty results. Disable both for the auto-launch — failures here
  # are best-effort, never fatal.
  set +e
  set +o pipefail 2>/dev/null || true

  chat "no terminal attached — opening the browser for you..."
  echo
  port=5050

  # Resolve full path to huntova binary; the detached child can't
  # rely on shell-prepended PATH being inherited.
  HV_BIN="$(command -v huntova 2>/dev/null)"
  if [ -z "$HV_BIN" ] && [ -x "$HOME/.local/bin/huntova" ]; then
    HV_BIN="$HOME/.local/bin/huntova"
  fi
  if [ -z "$HV_BIN" ]; then
    warn "huntova binary not on PATH — open a new terminal and run: huntova onboard"
    exit 0
  fi

  # Free :5050 if a stale server is bound (common — broken prior
  # install left one running). Best-effort.
  pid_on_port=$(lsof -ti tcp:"$port" 2>/dev/null | head -1)
  if [ -n "$pid_on_port" ]; then
    kill -9 "$pid_on_port" 2>/dev/null
    sleep 1
  fi

  mkdir -p "$HOME/.local/share/huntova" 2>/dev/null || true
  log_file="$HOME/.local/share/huntova/install-launch.log"

  # Spawn detached — setsid (Linux) preferred, nohup (macOS) fallback.
  if command -v setsid >/dev/null 2>&1; then
    setsid "$HV_BIN" serve --no-browser --port "$port" \
      >"$log_file" 2>&1 < /dev/null &
  else
    nohup "$HV_BIN" serve --no-browser --port "$port" \
      >"$log_file" 2>&1 < /dev/null &
  fi
  server_pid=$!
  disown 2>/dev/null || true

  # Poll /api/runtime up to 15s. Server boots in ~3-5s typically;
  # first-time DB schema init can stretch to 8-10s.
  ready=0
  for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    if curl -fsS "http://127.0.0.1:${port}/api/runtime" >/dev/null 2>&1; then
      ready=1; break
    fi
    sleep 1
  done

  if [ "$ready" -eq 0 ]; then
    warn "server didn't respond in 15s — open a new terminal and run: huntova onboard"
    echo
    printf "  %slast 20 lines of the server log (%s):%s\n" "$DIM" "$log_file" "$RESET"
    tail -n 20 "$log_file" 2>/dev/null
    exit 0
  fi

  # Land on the dashboard, not the wizard. The dashboard's empty state
  # offers "🪄 Auto Wizard" (newbie) + "Configure in Settings" (pro)
  # buttons so the user picks their own onboarding cadence.
  url="http://127.0.0.1:${port}/"
  case "$(uname -s 2>/dev/null)" in
    Darwin) open "$url" >/dev/null 2>&1 && ok "browser opened at $url" || warn "couldn't open browser — visit $url manually" ;;
    Linux)
      if command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$url" >/dev/null 2>&1 && ok "browser opened at $url" || warn "couldn't open — visit $url manually"
      elif command -v wslview >/dev/null 2>&1; then
        wslview "$url" >/dev/null 2>&1 && ok "browser opened at $url" || warn "visit $url manually"
      else
        warn "couldn't auto-open browser — visit $url manually"
      fi ;;
    *) warn "couldn't auto-open browser — visit $url manually" ;;
  esac
  echo
  printf "  %sserver running in background (PID %s).%s\n" "$DIM" "$server_pid" "$RESET"
  printf "  %sstop with: kill %s%s\n" "$DIM" "$server_pid" "$RESET"
  echo
fi
