"""huntova schedule — generate cron / launchd / systemd snippets for
the daily outreach cadence (sequence + inbox + pulse).

`huntova schedule print` emits a copy-pasteable snippet for the
user's OS:

  - macOS  → launchd LaunchAgent plist (StartCalendarInterval)
  - Linux  → systemd user timer + service unit
  - Other  → vanilla cron line

The snippet runs:

    huntova sequence run --max 25
    huntova inbox check
    huntova pulse

once per day at the user-supplied --at HH:MM (default 09:00 local).
Output goes to stdout so the user can pipe it into the right place.
We don't auto-install yet — `print` is opt-in by design (no
silent persistence on the user's machine).
"""

from __future__ import annotations

import argparse
import platform
import shutil
import sys
from pathlib import Path


def _bold(s: str) -> str: return f"\033[1m{s}\033[0m"
def _dim(s: str) -> str: return f"\033[2m{s}\033[0m"
def _cyan(s: str) -> str: return f"\033[36m{s}\033[0m"
def _green(s: str) -> str: return f"\033[32m{s}\033[0m"


_DEFAULT_LABEL = "com.huntova.daily"
_LOG_REL = "~/.local/share/huntova/logs/schedule.log"


def _resolve_huntova_bin() -> str:
    """Best-effort path to the installed huntova binary."""
    found = shutil.which("huntova")
    if found:
        return found
    # Fallback to pipx-default location.
    return str(Path.home() / ".local" / "pipx" / "venvs" / "huntova" / "bin" / "huntova")


def _parse_at(at: str) -> tuple[int, int]:
    at = (at or "09:00").strip()
    try:
        h, m = at.split(":", 1)
        hh = max(0, min(int(h), 23))
        mm = max(0, min(int(m), 59))
        return hh, mm
    except Exception:
        return 9, 0


def _build_chain(bin_path: str, max_send: int, with_update: bool = True) -> str:
    """Single shell command chaining the daily commands.
    Each runs sequentially; failures don't stop the chain, so a flaky
    inbox check doesn't skip pulse. When with_update is True (default),
    `huntova update --check` runs first — silent unless an upgrade is
    available, so the user sees a one-line nudge in their daily log
    when behind. Pair with a weekly `huntova update` cron / launchd
    job for full hands-off mode."""
    update_step = f"{bin_path} update --check; " if with_update else ""
    return (f"{update_step}"
            f"{bin_path} sequence run --max {max_send}; "
            f"{bin_path} inbox check; "
            f"{bin_path} pulse --since 1d")


# ── per-OS emitters ────────────────────────────────────────────────

def _emit_launchd(at: str, max_send: int, label: str) -> str:
    hh, mm = _parse_at(at)
    bin_path = _resolve_huntova_bin()
    log = _LOG_REL.replace("~", str(Path.home()))
    chain = _build_chain(bin_path, max_send)
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
      <string>/bin/sh</string>
      <string>-lc</string>
      <string>{chain}</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
      <key>Hour</key>     <integer>{hh}</integer>
      <key>Minute</key>   <integer>{mm:02d}</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>{log}</string>
    <key>StandardErrorPath</key>
    <string>{log}</string>
    <key>RunAtLoad</key><false/>
  </dict>
</plist>
"""
    return plist


def _emit_systemd(at: str, max_send: int, label: str) -> tuple[str, str, str]:
    """Return (timer_path, service_path, install_hint) trio of strings.
    The user pipes the contents into ~/.config/systemd/user/."""
    hh, mm = _parse_at(at)
    bin_path = _resolve_huntova_bin()
    log = _LOG_REL.replace("~", str(Path.home()))
    chain = _build_chain(bin_path, max_send)
    service = f"""[Unit]
Description=Huntova daily outreach worker (sequence + inbox + pulse)

[Service]
Type=oneshot
ExecStart=/bin/sh -lc '{chain}'
StandardOutput=append:{log}
StandardError=append:{log}
"""
    timer = f"""[Unit]
Description=Run Huntova daily outreach worker once a day

[Timer]
OnCalendar=*-*-* {hh:02d}:{mm:02d}:00
Persistent=true
Unit={label}.service

[Install]
WantedBy=timers.target
"""
    install = (
        f"# install:\n"
        f"#   cat > ~/.config/systemd/user/{label}.service <<'EOF'\n"
        f"# (paste service unit above)\n"
        f"#   EOF\n"
        f"#   cat > ~/.config/systemd/user/{label}.timer <<'EOF'\n"
        f"# (paste timer unit above)\n"
        f"#   EOF\n"
        f"#   systemctl --user daemon-reload\n"
        f"#   systemctl --user enable --now {label}.timer\n"
    )
    return timer, service, install


def _emit_cron(at: str, max_send: int) -> str:
    hh, mm = _parse_at(at)
    bin_path = _resolve_huntova_bin()
    log = _LOG_REL.replace("~", str(Path.home()))
    chain = _build_chain(bin_path, max_send)
    return (f"# Append this line to your crontab (`crontab -e`):\n"
            f"{mm} {hh} * * * {chain} >> {log} 2>&1\n")


# ── subcommand handler ─────────────────────────────────────────────

def _cmd_print(args: argparse.Namespace) -> int:
    target = (args.target or "").strip().lower() or _detect_target()
    at = (args.at or "09:00")
    max_send = max(1, min(int(args.max or 25), 100))
    label = args.label or _DEFAULT_LABEL

    if target == "launchd":
        plist = _emit_launchd(at, max_send, label)
        print(_bold(f"# launchd plist — save to ~/Library/LaunchAgents/{label}.plist:\n"))
        print(plist)
        print(_dim(f"# install:"))
        print(_dim(f"#   launchctl unload ~/Library/LaunchAgents/{label}.plist 2>/dev/null"))
        print(_dim(f"#   launchctl load   ~/Library/LaunchAgents/{label}.plist"))
        return 0

    if target == "systemd":
        timer, service, hint = _emit_systemd(at, max_send, label)
        print(_bold(f"# {label}.service — paste into ~/.config/systemd/user/{label}.service:\n"))
        print(service)
        print(_bold(f"# {label}.timer — paste into ~/.config/systemd/user/{label}.timer:\n"))
        print(timer)
        print(hint)
        return 0

    if target == "cron":
        print(_emit_cron(at, max_send))
        return 0

    print(f"[huntova] unknown target {target!r}. Try one of: launchd, systemd, cron.",
          file=sys.stderr)
    return 1


def _detect_target() -> str:
    sys_name = platform.system().lower()
    if sys_name == "darwin":
        return "launchd"
    if sys_name == "linux":
        # systemd if user units dir is reachable; otherwise fall back to cron.
        if Path.home().joinpath(".config", "systemd", "user").exists() or shutil.which("systemctl"):
            return "systemd"
        return "cron"
    return "cron"


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "schedule",
        help="emit launchd / systemd / cron snippet for daily outreach",
        description=(
            "Generate the OS-native scheduled-job config for running "
            "`huntova sequence run`, `huntova inbox check`, and "
            "`huntova pulse` once per day. We don't auto-install — "
            "you copy the snippet into the right place. macOS gets a "
            "launchd LaunchAgent plist, Linux gets a systemd user "
            "timer + service, anything else gets a cron line."
        ),
        epilog=(
            "Examples:\n"
            "  huntova schedule print                       # auto-detect OS\n"
            "  huntova schedule print --target launchd --at 09:30\n"
            "  huntova schedule print --target cron --max 50\n\n"
            "Output goes to stdout so you can pipe it:\n"
            "  huntova schedule print > ~/Library/LaunchAgents/com.huntova.daily.plist\n"
            "  launchctl load ~/Library/LaunchAgents/com.huntova.daily.plist\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="schedule_cmd", required=True)

    p_print = sub.add_parser("print", help="print the snippet to stdout")
    p_print.add_argument("--target", default="",
                         choices=("", "launchd", "systemd", "cron"),
                         help="OS scheduler (auto-detected when omitted)")
    p_print.add_argument("--at", default="09:00",
                         help="local time HH:MM [09:00]")
    p_print.add_argument("--max", default="25",
                         help="max emails to send per run [25]")
    p_print.add_argument("--label", default=_DEFAULT_LABEL,
                         help=f"job label / service name [{_DEFAULT_LABEL}]")
    p_print.set_defaults(func=_cmd_print)
