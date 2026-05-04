"""Daemon installer — keeps `huntova serve` running across reboots.

Idiomatic Python ports of the launchd plist and systemd user-unit
patterns OpenClaw uses for their gateway daemon. We don't copy any
of their actual TypeScript; this is a fresh implementation of the
abstract pattern.

Surface:
    install_daemon(port, environment) — writes the platform-specific
        unit file + loads/enables it. Returns the unit path.
    uninstall_daemon() — unloads + removes the unit file.
    daemon_status() — returns "running" / "stopped" / "not-installed".
    start_daemon() / stop_daemon() — runtime control.

Cross-platform:
    macOS  → ~/Library/LaunchAgents/com.huntova.gateway.plist
    Linux  → ~/.config/systemd/user/huntova.service
    Win    → not supported yet (returns "windows-unsupported")
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from html import escape as _xml_escape
from pathlib import Path

DAEMON_LABEL = "com.huntova.gateway"
DAEMON_DESCRIPTION = "Huntova gateway — local-first lead-gen agent"


# ── Path resolution ────────────────────────────────────────────────


def _macos_plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{DAEMON_LABEL}.plist"


def _linux_unit_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "systemd" / "user" / "huntova.service"


def _log_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    p = Path(base) / "huntova" / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _resolve_huntova_binary() -> str:
    """Find the huntova CLI binary path. Returns absolute path."""
    return shutil.which("huntova") or sys.argv[0]


# ── launchd plist (macOS) ──────────────────────────────────────────


def _build_plist(port: int, environment: dict[str, str] | None = None) -> str:
    """Generate a launchd LaunchAgent plist. Idiomatic Python
    implementation of the standard Apple plist template."""
    env = environment or {}
    huntova = _resolve_huntova_binary()
    log_dir = _log_dir()
    stdout_path = str(log_dir / "daemon.out")
    stderr_path = str(log_dir / "daemon.err")
    args_xml = "\n      ".join(
        f"<string>{_xml_escape(arg)}</string>"
        for arg in (huntova, "serve", "--host", "127.0.0.1",
                    "--port", str(port), "--no-browser")
    )
    env_xml = ""
    if env:
        entries = "\n      ".join(
            f"<key>{_xml_escape(k)}</key>\n      <string>{_xml_escape(v)}</string>"
            for k, v in env.items() if v
        )
        if entries:
            env_xml = f"\n    <key>EnvironmentVariables</key>\n    <dict>\n      {entries}\n    </dict>"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        '  <dict>\n'
        f'    <key>Label</key>\n    <string>{DAEMON_LABEL}</string>\n'
        '    <key>RunAtLoad</key>\n    <true/>\n'
        '    <key>KeepAlive</key>\n    <true/>\n'
        '    <key>ThrottleInterval</key>\n    <integer>10</integer>\n'
        '    <key>ProcessType</key>\n    <string>Background</string>\n'
        '    <key>ProgramArguments</key>\n'
        f'    <array>\n      {args_xml}\n    </array>\n'
        f'    <key>StandardOutPath</key>\n    <string>{_xml_escape(stdout_path)}</string>\n'
        f'    <key>StandardErrorPath</key>\n    <string>{_xml_escape(stderr_path)}</string>'
        f'{env_xml}\n'
        '  </dict>\n'
        '</plist>\n'
    )


def _macos_install(port: int, env: dict[str, str] | None) -> tuple[bool, str]:
    plist_path = _macos_plist_path()
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    plist_path.write_text(_build_plist(port, env))
    try:
        plist_path.chmod(0o600)
    except OSError:
        pass
    # Bootstrap the agent into launchd. `launchctl load` is the
    # legacy interface; `launchctl bootstrap gui/<uid>` is the
    # modern one. We use load -w for portability across macOS
    # versions.
    try:
        subprocess.run(["launchctl", "unload", str(plist_path)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       check=False)
        rc = subprocess.run(["launchctl", "load", "-w", str(plist_path)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, check=False)
        if rc.returncode != 0:
            return False, f"launchctl load failed: {rc.stderr.strip() or rc.stdout.strip()}"
        return True, str(plist_path)
    except FileNotFoundError:
        return False, "launchctl not found (is this really macOS?)"


def _macos_uninstall() -> tuple[bool, str]:
    plist_path = _macos_plist_path()
    if not plist_path.exists():
        return True, "(not installed)"
    subprocess.run(["launchctl", "unload", str(plist_path)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   check=False)
    plist_path.unlink(missing_ok=True)
    return True, "uninstalled"


def _macos_status() -> str:
    plist_path = _macos_plist_path()
    if not plist_path.exists():
        return "not-installed"
    try:
        rc = subprocess.run(["launchctl", "list", DAEMON_LABEL],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, check=False, timeout=5)
        if rc.returncode == 0 and DAEMON_LABEL in (rc.stdout or ""):
            # Parse the PID line: `"PID" = N;`
            for line in (rc.stdout or "").splitlines():
                line = line.strip()
                if line.startswith('"PID"') and "=" in line:
                    pid_str = line.split("=", 1)[1].strip().rstrip(";").strip()
                    if pid_str and pid_str != "-":
                        return "running"
            return "stopped"
        return "stopped"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return "unknown"


# ── systemd user unit (Linux) ──────────────────────────────────────


def _build_systemd_unit(port: int, environment: dict[str, str] | None = None) -> str:
    """Generate a systemd user unit file. Idiomatic Python
    implementation of the standard unit-file template."""
    huntova = _resolve_huntova_binary()
    env_lines = []
    if environment:
        for k, v in environment.items():
            if not v or "\n" in v or "\r" in v:
                continue
            # systemd needs careful escaping for special chars
            env_lines.append(f'Environment="{k}={v}"')
    env_block = ("\n".join(env_lines) + "\n") if env_lines else ""
    log_dir = _log_dir()
    return (
        "[Unit]\n"
        f"Description={DAEMON_DESCRIPTION}\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n"
        "StartLimitBurst=5\n"
        "StartLimitIntervalSec=60\n"
        "\n"
        "[Service]\n"
        f"ExecStart={huntova} serve --host 127.0.0.1 --port {port} --no-browser\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        "TimeoutStopSec=30\n"
        "StandardOutput=append:" + str(log_dir / "daemon.out") + "\n"
        "StandardError=append:" + str(log_dir / "daemon.err") + "\n"
        f"{env_block}"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def _linux_install(port: int, env: dict[str, str] | None) -> tuple[bool, str]:
    if not shutil.which("systemctl"):
        return False, "systemctl not found (systemd-only Linux distros supported)"
    unit_path = _linux_unit_path()
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text(_build_systemd_unit(port, env))
    try:
        unit_path.chmod(0o600)
    except OSError:
        pass
    # Reload, enable, start
    try:
        subprocess.run(["systemctl", "--user", "daemon-reload"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       check=False)
        subprocess.run(["systemctl", "--user", "enable", "huntova.service"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       check=False)
        rc = subprocess.run(["systemctl", "--user", "restart", "huntova.service"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, check=False)
        if rc.returncode != 0:
            return False, f"systemctl restart failed: {rc.stderr.strip() or rc.stdout.strip()}"
        return True, str(unit_path)
    except FileNotFoundError:
        return False, "systemctl not found"


def _linux_uninstall() -> tuple[bool, str]:
    unit_path = _linux_unit_path()
    if not unit_path.exists():
        return True, "(not installed)"
    subprocess.run(["systemctl", "--user", "stop", "huntova.service"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   check=False)
    subprocess.run(["systemctl", "--user", "disable", "huntova.service"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   check=False)
    unit_path.unlink(missing_ok=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   check=False)
    return True, "uninstalled"


def _linux_status() -> str:
    if not _linux_unit_path().exists():
        return "not-installed"
    try:
        rc = subprocess.run(["systemctl", "--user", "is-active", "huntova.service"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, check=False, timeout=5)
        out = (rc.stdout or "").strip()
        if out == "active":
            return "running"
        if out in ("inactive", "deactivating", "activating"):
            return "stopped"
        return "unknown"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return "unknown"


# ── Public API ─────────────────────────────────────────────────────


def install_daemon(port: int = 5050,
                   environment: dict[str, str] | None = None) -> tuple[bool, str]:
    """Install the daemon. Returns (success, path-or-error-message)."""
    sysname = platform.system().lower()
    if sysname == "darwin":
        return _macos_install(port, environment)
    if sysname == "linux":
        return _linux_install(port, environment)
    if sysname == "windows":
        return False, "windows-unsupported (use Task Scheduler manually for now)"
    return False, f"unsupported-platform: {sysname}"


def uninstall_daemon() -> tuple[bool, str]:
    """Uninstall the daemon. Idempotent."""
    sysname = platform.system().lower()
    if sysname == "darwin":
        return _macos_uninstall()
    if sysname == "linux":
        return _linux_uninstall()
    return False, f"unsupported-platform: {sysname}"


def daemon_status() -> str:
    """Returns one of: running / stopped / not-installed / unknown /
    unsupported."""
    sysname = platform.system().lower()
    if sysname == "darwin":
        return _macos_status()
    if sysname == "linux":
        return _linux_status()
    if sysname == "windows":
        return "unsupported"
    return "unknown"


def start_daemon() -> tuple[bool, str]:
    """Start the daemon (assumes it's already installed)."""
    sysname = platform.system().lower()
    if sysname == "darwin":
        plist_path = _macos_plist_path()
        if not plist_path.exists():
            return False, "not installed — run `huntova daemon install` first"
        rc = subprocess.run(["launchctl", "load", "-w", str(plist_path)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, check=False)
        return rc.returncode == 0, (rc.stderr.strip() or "started")
    if sysname == "linux":
        rc = subprocess.run(["systemctl", "--user", "start", "huntova.service"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, check=False)
        return rc.returncode == 0, (rc.stderr.strip() or "started")
    return False, f"unsupported-platform: {sysname}"


def stop_daemon() -> tuple[bool, str]:
    """Stop the daemon (without uninstalling)."""
    sysname = platform.system().lower()
    if sysname == "darwin":
        plist_path = _macos_plist_path()
        if not plist_path.exists():
            return True, "(not installed)"
        rc = subprocess.run(["launchctl", "unload", str(plist_path)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, check=False)
        return rc.returncode == 0, (rc.stderr.strip() or "stopped")
    if sysname == "linux":
        rc = subprocess.run(["systemctl", "--user", "stop", "huntova.service"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, check=False)
        return rc.returncode == 0, (rc.stderr.strip() or "stopped")
    return False, f"unsupported-platform: {sysname}"
