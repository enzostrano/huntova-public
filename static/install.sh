#!/usr/bin/env bash
# Huntova one-shot installer — pipx-based.
# Usage: curl -fsSL https://huntova.com/install.sh | sh
#
# Detects the user's Python toolchain, installs pipx if missing, then
# installs Huntova as an isolated CLI. Doesn't touch the user's
# global Python. Idempotent — safe to re-run for upgrades.

set -e

# ── Pretty output ─────────────────────────────────────────────────
ESC=$'\033'
RESET="${ESC}[0m"; BOLD="${ESC}[1m"; DIM="${ESC}[2m"
GREEN="${ESC}[32m"; RED="${ESC}[31m"; YELLOW="${ESC}[33m"
PURPLE="${ESC}[35m"; CYAN="${ESC}[36m"

box_top()    { printf '%s╭─ %s ──────────────────────────────────────────╮%s\n' "$PURPLE" "$1" "$RESET"; }
box_mid()    { printf '%s│%s %s\n' "$PURPLE" "$RESET" "$1"; }
box_bottom() { printf '%s╰──────────────────────────────────────────────────╯%s\n' "$PURPLE" "$RESET"; }

step() { printf '%s▸%s %s\n' "$CYAN" "$RESET" "$1"; }
ok()   { printf '%s✓%s %s\n' "$GREEN" "$RESET" "$1"; }
warn() { printf '%s!%s %s\n' "$YELLOW" "$RESET" "$1"; }
die()  { printf '%s✗%s %s\n' "$RED" "$RESET" "$1" >&2; exit 1; }

echo
box_top "Huntova installer"
box_mid "Local-first BYOK lead-gen agent. ~30 second install."
box_bottom
echo

# ── Pre-flight: Python ────────────────────────────────────────────
step "Detecting Python toolchain"
if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  die "Python 3.11+ is required. Install from https://www.python.org/downloads/ then re-run."
fi

PY_VER=$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 11 ]; }; then
  die "Python $PY_VER detected. Huntova requires Python 3.11 or newer."
fi
ok "Python ${PY_VER} (${BOLD}${PY}${RESET})"

# ── pipx ──────────────────────────────────────────────────────────
if ! command -v pipx >/dev/null 2>&1; then
  step "Installing pipx (isolated Python tool runner)"
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
if pipx list 2>/dev/null | grep -q '^\s*package huntova\s'; then
  step "Upgrading Huntova"
  pipx upgrade huntova >/dev/null 2>&1 || pipx install --force huntova >/dev/null 2>&1
else
  step "Installing Huntova from PyPI"
  pipx install huntova >/dev/null 2>&1 || die "pipx install huntova failed"
fi
ok "Huntova installed"

# ── Optional: Playwright Chromium ─────────────────────────────────
# Playwright is a hunt-quality dependency, not a hard one. The agent
# falls back to requests-only mode without Chromium, but lead quality
# drops. Offer to install if running interactively.
if [ -t 0 ] && [ -t 1 ]; then
  echo
  printf "%s?%s Install Playwright Chromium for deep-qualify (recommended) [Y/n]: " "$CYAN" "$RESET"
  read -r ans
  ans="${ans:-y}"
  if [ "$ans" = "y" ] || [ "$ans" = "Y" ]; then
    step "Installing Playwright Chromium (~120MB)"
    pipx inject huntova playwright >/dev/null 2>&1 || true
    pipx run --spec huntova playwright install chromium >/dev/null 2>&1 \
      || warn "Playwright Chromium install failed — agent will run in requests-only mode"
    ok "Playwright ready"
  fi
fi

# ── Done ──────────────────────────────────────────────────────────
echo
box_top "Installation complete"
box_mid ""
box_mid "Next: ${BOLD}huntova onboard${RESET}"
box_mid ""
box_mid "  Step-by-step wizard. Picks provider, saves your key,"
box_mid "  opens the dashboard. Recommended for first-time users."
box_mid ""
box_mid "Or skip the wizard:"
box_mid "  ${DIM}export HV_GEMINI_KEY=…${RESET}"
box_mid "  ${DIM}huntova hunt --max-leads 5${RESET}"
box_mid ""
box_bottom
echo
printf "  Docs:   %s\n" "${CYAN}https://huntova.com/download${RESET}"
printf "  Issues: %s\n" "${CYAN}https://github.com/enzostrano/huntova-public/issues${RESET}"
echo

# Auto-launch the wizard if interactive
if [ -t 0 ] && [ -t 1 ]; then
  printf "%s?%s Run %shuntova onboard%s now? [Y/n]: " "$CYAN" "$RESET" "$BOLD" "$RESET"
  read -r run_ans
  run_ans="${run_ans:-y}"
  if [ "$run_ans" = "y" ] || [ "$run_ans" = "Y" ]; then
    exec huntova onboard
  fi
fi
