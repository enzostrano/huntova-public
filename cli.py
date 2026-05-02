"""
Huntova CLI — local-first BYOK lead-gen entry point.

Usage:
    huntova               # alias for `huntova serve`
    huntova serve         # boot local FastAPI on 127.0.0.1:5050, open browser
    huntova onboard       # rich first-run wizard (TUI or web; saves key to keychain)
    huntova doctor        # diagnostic dump (env, db reachability, deps)
    huntova version       # print version

The serve command sets APP_MODE=local before importing server, so the
existing FastAPI app comes up in single-user no-billing no-auth shape
without any code changes to server.py.

Stdlib argparse so `pipx install huntova` doesn't pull Click/Typer.
"""
from __future__ import annotations

import argparse
import os
import sys
import webbrowser
from pathlib import Path

VERSION = "0.1.0a291"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5050  # avoid clashing with the cloud's :5000


def _config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    p = Path(base) / "huntova"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _hydrate_env_from_local_config() -> str:
    """Push secrets + config from disk into os.environ before any
    server module imports. The existing config.py / app.py / server.py
    code reads env vars at import time, so we have to populate them
    here.

    Returns the preferred_provider name (or "anthropic" default).
    """
    preferred = "anthropic"
    # 1) secrets_store (keychain or encrypted file). All 13 provider keys
    # need to round-trip across processes — saving via `huntova onboard`
    # then running a fresh `huntova hunt` should find the key. Plus the
    # custom-provider base_url and model so non-OpenAI users don't lose
    # their endpoint config across CLI invocations.
    try:
        from secrets_store import get_secret
        from providers import _ENV_KEY as _PROVIDER_ENV
        env_vars: list[str] = list(_PROVIDER_ENV.values()) + [
            "HV_CUSTOM_BASE_URL", "HV_CUSTOM_MODEL",
            # Auto-generated session-signing key (set by `huntova onboard`
            # on first run) — needs to round-trip across processes so
            # the dev-fallback warning doesn't fire on every CLI invocation.
            "HV_SECRET_KEY",
        ]
        for env_var in env_vars:
            if not os.environ.get(env_var):
                val = get_secret(env_var)
                if val:
                    os.environ[env_var] = val
    except Exception as e:
        # Surface the failure ONCE per machine. Locked keychain (macOS
        # daemon context, GUI keychain prompt timeout), broken
        # `cryptography` install, or permission-denied on secrets.enc
        # all dead-end here. User saw "no API key configured" and was
        # told to re-onboard, even though the key was sitting right
        # there. Sentinel file in the config dir prevents the warning
        # from spamming every CLI invocation. Cleared automatically on
        # the first successful keychain read (see secrets_store.get_secret).
        sentinel = _config_dir() / ".keychain_warned"
        if not sentinel.exists():
            try:
                sentinel.parent.mkdir(parents=True, exist_ok=True)
                sentinel.touch()
            except OSError:
                pass
            print(
                f"[huntova] keychain read failed ({type(e).__name__}: "
                f"{str(e)[:100]}) — falling back to env vars only. "
                f"Run `huntova doctor` for details. (This warning won't "
                f"repeat — delete {sentinel} to re-enable.)",
                file=sys.stderr,
            )
    # 2) config.toml — explicit values win over env if not yet set
    try:
        import tomllib  # type: ignore[import-not-found]
        cfg = _config_dir() / "config.toml"
        if cfg.exists():
            with open(cfg, "rb") as f:
                data = tomllib.load(f)
            for k, v in data.items():
                if k.startswith("HV_") and isinstance(v, (str, int, float, bool)) and not os.environ.get(k):
                    os.environ[k] = str(v)
            p = data.get("preferred_provider")
            if isinstance(p, str) and p:
                preferred = p
    except Exception:
        pass
    # 3) Tell config.py which provider to use. config.py recognises
    # "gemini", "openai", "anthropic", or "local" via HV_AI_PROVIDER.
    if not os.environ.get("HV_AI_PROVIDER"):
        os.environ["HV_AI_PROVIDER"] = preferred
    return preferred


def cmd_serve(args: argparse.Namespace) -> int:
    """Boot the FastAPI app on localhost in local mode.

    Plain `huntova serve` Just Works — no DATABASE_URL needed (SQLite
    at ~/.local/share/huntova/db.sqlite by default). Reads API keys
    from the keychain / encrypted store / config.toml and pushes them
    into os.environ so the server's existing AI client picks them up.
    """
    os.environ.setdefault("APP_MODE", "local")
    preferred = _hydrate_env_from_local_config()
    # Friendly heads-up if no key is configured yet. Use the canonical
    # provider resolver so users with any of the 13 supported providers
    # (Anthropic, OpenAI, Gemini, OpenRouter, Groq, DeepSeek, Together,
    # Mistral, Perplexity, Ollama, LM Studio, llamafile, custom) don't
    # get a false "no key" message.
    has_key = False
    try:
        from providers import list_available_providers
        has_key = bool(list_available_providers())
    except Exception:
        has_key = any(
            os.environ.get(v)
            for v in ("HV_ANTHROPIC_KEY", "HV_OPENAI_KEY", "HV_GEMINI_KEY")
        )
    if not has_key:
        print("[huntova] no API key configured — run `huntova onboard` first "
              "or set HV_ANTHROPIC_KEY (default) / HV_OPENAI_KEY / HV_GEMINI_KEY")
    # Show the update banner before we open the browser so the user
    # sees it in the terminal even if the dashboard tab grabs focus.
    if not getattr(args, "no_update_check", False):
        try:
            _maybe_prompt_update()
        except Exception:
            pass  # silent — never block server boot on a network blip
    import uvicorn  # noqa: F401  — import after env-var setup
    host = args.host or DEFAULT_HOST
    port = int(args.port or DEFAULT_PORT)
    backend = "PostgreSQL" if os.environ.get("DATABASE_URL") else "SQLite (~/.local/share/huntova/db.sqlite)"
    print(f"[huntova] provider: {preferred}    storage: {backend}")
    print(f"[huntova] starting local server on http://{host}:{port}")
    print(f"[huntova] tip: closing this terminal stops the server. Try `huntova run` "
          f"for an interactive console, or `huntova daemon install` for headless.")
    # a284: --logs spawns a tail thread that reads the SSE event stream
    # and prints colored event lines in this same terminal. Useful so
    # the operator can SEE what Huntova is doing instead of just a
    # uvicorn access log.
    if getattr(args, "logs", False):
        try:
            from cli_terminal import attach_log_tail
            attach_log_tail(host, port)
        except Exception as _e:
            print(f"[huntova] couldn't attach log tail: {_e}")
    if not args.no_browser:
        # Default landing is the dashboard at /. Even on first-run with
        # no provider configured, the dashboard renders with a friendly
        # empty-state banner offering "🪄 Auto Wizard" (newbie path) +
        # "Configure in Settings" (pro path). Forces /setup only when
        # `huntova onboard --browser` explicitly sets setup_first=True.
        force_setup = bool(getattr(args, "setup_first", False))
        landing = "/setup" if force_setup else "/"
        try:
            webbrowser.open_new_tab(f"http://{host}:{port}{landing}")
        except Exception:
            pass
    uvicorn.run("server:app", host=host, port=port, log_level="info")
    return 0


_PROVIDERS = (
    ("anthropic", "Anthropic Claude (default — highest quality scoring + email drafting)",
     "https://console.anthropic.com/settings/keys", "HV_ANTHROPIC_KEY"),
    ("gemini", "Google Gemini (fastest, free tier available)",
     "https://aistudio.google.com/apikey", "HV_GEMINI_KEY"),
    ("openai", "OpenAI GPT (broad model selection)",
     "https://platform.openai.com/api-keys", "HV_OPENAI_KEY"),
)


def _prompt(question: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        ans = input(f"{question}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return ans or default


def _prompt_choice(question: str, choices: list[str], default_idx: int = 0) -> str:
    print(question)
    for i, c in enumerate(choices, 1):
        marker = "*" if i - 1 == default_idx else " "
        print(f"  {marker} {i}. {c}")
    while True:
        raw = _prompt("Choice", str(default_idx + 1))
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(choices):
                return choices[idx]
        except ValueError:
            pass
        print("Pick a number from the list.")


def _ansi(s: str, code: str) -> str:
    """Wrap text in ANSI colour escape if stdout is a TTY."""
    if not sys.stdout.isatty():
        return s
    return f"\033[{code}m{s}\033[0m"


def _box_top(width: int = 60, title: str = "") -> str:
    if title:
        title = f" {title} "
        pad = max(0, width - 4 - len(title))
        return f"╭─{title}{'─' * pad}─╮"
    return "╭" + "─" * (width - 2) + "╮"


def _box_mid(text: str = "", width: int = 60) -> str:
    pad = max(0, width - 4 - len(text))
    return f"│ {text}{' ' * pad} │"


def _box_bottom(width: int = 60) -> str:
    return "╰" + "─" * (width - 2) + "╯"


def _spinner_step(label: str, fn) -> tuple[bool, str]:
    """Run a function with a polished status indicator. Returns (ok, msg)."""
    import threading, time
    stop = {"go": True}
    result = {"value": None, "err": None}
    spinner_chars = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    is_tty = sys.stdout.isatty()

    def runner():
        try:
            result["value"] = fn()
        except Exception as e:
            result["err"] = e

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    if is_tty:
        i = 0
        while t.is_alive():
            ch = spinner_chars[i % len(spinner_chars)]
            sys.stdout.write(f"\r  {_ansi(ch, '36')} {label}…")
            sys.stdout.flush()
            time.sleep(0.08)
            i += 1
        sys.stdout.write("\r  ")
    t.join()
    if result["err"]:
        msg = f"{type(result['err']).__name__}: {str(result['err'])[:80]}"
        sys.stdout.write(f"{_ansi('✗', '31')} {label} — {msg}\n")
        return False, msg
    sys.stdout.write(f"{_ansi('✓', '32')} {label}\n")
    return True, str(result["value"]) if result["value"] is not None else ""


def cmd_status(args: argparse.Namespace) -> int:
    """Operational dashboard — what's configured, what's running, recent activity.

    One screen showing the state of every subsystem:
      Daemon, Server, Provider, SearXNG, Local AI, Plugins,
      Database (lead count, hunt count), Telemetry,
      Last hunt time / lead total.

    Examples:
        huntova status              # pretty terminal output
        huntova status --json       # machine-readable
    """
    try:
        from tui import bold, dim, green, red, yellow, cyan, purple
    except ImportError:
        bold = dim = green = red = yellow = cyan = purple = lambda s: s

    user_id = _bootstrap_local_env()
    if user_id is None:
        return 1
    import asyncio as _asyncio
    import db as _db
    import json as _json

    report: dict = {}

    # 1. Daemon state
    try:
        import huntova_daemon as _daemon
        report["daemon"] = {"status": _daemon.daemon_status()}
    except Exception as e:
        report["daemon"] = {"status": "unknown", "error": str(e)[:80]}

    # 2. Server reachability (probes the default port). Pass timeout
    # explicitly to urlopen — never `socket.setdefaulttimeout`, which
    # leaks process-globally and breaks subsequent network calls.
    try:
        import urllib.request
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{DEFAULT_PORT}/api/runtime",
                                         timeout=0.5) as r:
                if r.status == 200:
                    report["server"] = {"status": "running",
                                          "url": f"http://127.0.0.1:{DEFAULT_PORT}/"}
                else:
                    report["server"] = {"status": f"http_{r.status}"}
        except Exception:
            report["server"] = {"status": "stopped"}
    except Exception as e:
        report["server"] = {"status": "unknown", "error": str(e)[:80]}

    # 3. Provider config (NO secrets leaked — only names)
    try:
        from providers import list_available_providers
        configured = list_available_providers() or []
        report["providers"] = {
            "configured": configured,
            "preferred": _resolve_preferred_provider(),
            "count": len(configured),
        }
    except Exception as e:
        report["providers"] = {"configured": [], "error": str(e)[:80]}

    # 4. SearXNG reachability
    searxng = os.environ.get("SEARXNG_URL", "").strip() or "http://127.0.0.1:8888"
    try:
        import urllib.request as _ur
        req = _ur.Request(searxng.rstrip("/") + "/search?q=test&format=json",
                           headers={"Accept": "application/json"})
        try:
            with _ur.urlopen(req, timeout=2) as r:
                report["searxng"] = {"url": searxng, "status": "reachable", "http": r.status}
        except Exception:
            report["searxng"] = {"url": searxng, "status": "unreachable"}
    except Exception as e:
        report["searxng"] = {"url": searxng, "status": "unknown", "error": str(e)[:80]}

    # 5. Local AI servers
    try:
        from providers import detect_local_servers
        local = detect_local_servers()
        report["local_ai"] = {
            name: ("detected" if info.get("available") else "not running")
            for name, info in local.items()
        }
    except Exception:
        report["local_ai"] = {}

    # 6. Plugins
    try:
        from plugins import get_registry
        reg = get_registry()
        try: reg.discover()
        except Exception: pass
        names = [p.get("name") if isinstance(p, dict) else getattr(p, "name", None)
                 for p in reg.list_plugins()]
        errors = []
        try: errors = reg.errors() or []
        except Exception: pass
        report["plugins"] = {
            "loaded_count": len([n for n in names if n]),
            "loaded": [n for n in names if n][:8],
            "load_errors": [{"slug": s, "msg": str(e)[:80]} for s, e in errors[:3]],
        }
    except Exception as e:
        report["plugins"] = {"error": str(e)[:80]}

    # 7. DB stats
    try:
        leads = _asyncio.run(_db.get_leads(user_id, limit=1))
        total_leads = _asyncio.run(_db.get_leads_count(user_id))
        try:
            history = _asyncio.run(_db.get_user_history(user_id, limit=1))
            last_hunt_at = history[0].get("started_at") if history else None
        except Exception:
            last_hunt_at = None
        report["data"] = {
            "lead_count": total_leads,
            "last_hunt_at": last_hunt_at,
            "has_data": bool(leads),
        }
    except Exception as e:
        report["data"] = {"error": str(e)[:80]}

    # 8. Telemetry consent
    try:
        flag_path = _telemetry_flag_path()
        report["telemetry"] = {"opted_in": flag_path.exists()}
    except Exception:
        report["telemetry"] = {"opted_in": False}

    # 9. Filesystem
    try:
        import db_driver as _dbd
        from secrets_store import _backend_label
        report["filesystem"] = {
            "config": str(_config_dir() / "config.toml"),
            "data": str(_dbd._local_db_path().parent),
            "secrets": _backend_label(),
        }
    except Exception:
        pass

    # 10. Outreach pipeline state — surfaces sequence/inbox/schedule
    # so users see at a glance what the autonomous-daily mode would
    # need before running `huntova schedule print`.
    try:
        leads_all = _asyncio.run(_db.get_leads(user_id, limit=2000)) or []
        seq_counts = {0: 0, 1: 0, 2: 0, 3: 0}
        seq_paused = 0
        for ld in leads_all:
            st = int(ld.get("_seq_step") or 0)
            seq_counts[st] = seq_counts.get(st, 0) + 1
            if ld.get("_seq_paused") or ld.get("email_status") in (
                    "replied", "won", "meeting_booked"):
                seq_paused += 1
        report["sequence"] = {
            "step1_opener": seq_counts.get(1, 0),
            "step2_bump":   seq_counts.get(2, 0),
            "step3_final":  seq_counts.get(3, 0),
            "paused":       seq_paused,
            "not_enrolled": seq_counts.get(0, 0),
        }
    except Exception as _seq_e:
        report["sequence"] = {"error": str(_seq_e)[:80]}

    # IMAP creds present? Don't probe live (slow), just check the
    # secrets store / env. Mirrors `cli_inbox._load_imap_settings`.
    try:
        _imap_host = os.environ.get("HV_IMAP_HOST")
        _imap_user = os.environ.get("HV_IMAP_USER")
        _imap_pass = os.environ.get("HV_IMAP_PASSWORD")
        if not (_imap_host and _imap_user and _imap_pass):
            try:
                from secrets_store import get_secret
                _imap_host = _imap_host or get_secret("HV_IMAP_HOST")
                _imap_user = _imap_user or get_secret("HV_IMAP_USER")
                _imap_pass = _imap_pass or get_secret("HV_IMAP_PASSWORD")
            except Exception:
                pass
        report["inbox"] = {
            "configured": bool(_imap_host and _imap_user and _imap_pass),
            "host": _imap_host or "",
        }
    except Exception:
        report["inbox"] = {"configured": False}

    # Scheduled job? Check standard launchd / systemd locations.
    try:
        from pathlib import Path as _P
        _label = "com.huntova.daily"
        _launchd = _P.home() / "Library" / "LaunchAgents" / f"{_label}.plist"
        _systemd = _P.home() / ".config" / "systemd" / "user" / f"{_label}.timer"
        installed = _launchd.exists() or _systemd.exists()
        report["schedule"] = {
            "installed": installed,
            "path": str(_launchd if _launchd.exists() else
                        _systemd if _systemd.exists() else ""),
        }
    except Exception:
        report["schedule"] = {"installed": False}

    if args.format == "json":
        print(_json.dumps(report, indent=2, default=str))
        return 0

    # Pretty render
    def _badge(text: str, kind: str) -> str:
        colors = {"ok": green, "warn": yellow, "err": red, "info": cyan,
                   "dim": dim}
        return colors.get(kind, dim)(text)

    print()
    print(f"  {bold('Huntova')} {dim('v' + _version_safe())}")
    print()

    # Daemon
    d = report.get("daemon", {}).get("status", "unknown")
    d_badge = {"running": _badge("● running", "ok"),
                "stopped": _badge("○ stopped", "warn"),
                "not-installed": _badge("○ not installed", "dim"),
                "unsupported": _badge("⚠ unsupported", "err")}.get(d, _badge("? unknown", "dim"))
    print(f"  daemon         {d_badge}")

    # Server
    s = report.get("server", {})
    s_badge = (_badge("● running on " + s.get("url", "?"), "ok") if s.get("status") == "running"
                else _badge("○ stopped", "warn"))
    print(f"  server         {s_badge}")

    # Providers
    pr = report.get("providers", {})
    if pr.get("configured"):
        names = ", ".join(pr["configured"][:5])
        if len(pr["configured"]) > 5:
            names += f" +{len(pr['configured']) - 5}"
        count_label = f"● {pr['count']} configured"
        print(f"  ai providers   {_badge(count_label, 'ok')}  {dim(names)}")
        if pr.get("preferred"):
            print(f"                 preferred: {bold(pr['preferred'])}")
    else:
        print(f"  ai providers   {_badge('○ none configured', 'warn')}  {dim('run `huntova onboard`')}")

    # SearXNG
    sx = report.get("searxng", {})
    sx_badge = (_badge("● reachable", "ok") if sx.get("status") == "reachable"
                 else _badge("○ unreachable", "warn"))
    print(f"  search (searxng) {sx_badge}  {dim(sx.get('url', ''))}")

    # Local AI
    la = report.get("local_ai", {})
    detected = [n for n, s in la.items() if s == "detected"]
    if detected:
        print(f"  local ai       {_badge('● ' + ', '.join(detected), 'ok')}")
    else:
        print(f"  local ai       {_badge('○ none running', 'dim')}")

    # Plugins
    pl = report.get("plugins", {})
    pl_count = pl.get("loaded_count", 0)
    if pl_count:
        names = ", ".join(pl.get("loaded", [])[:6])
        print(f"  plugins        {_badge(f'● {pl_count} loaded', 'ok')}  {dim(names)}")
        if pl.get("load_errors"):
            for err in pl["load_errors"]:
                print(f"                 {_badge('⚠ ' + err.get('slug','?') + ': ' + err.get('msg','?'), 'err')}")
    else:
        print(f"  plugins        {_badge('○ none loaded', 'warn')}")

    # Data
    dt = report.get("data", {})
    if dt.get("lead_count") is not None:
        leads_label = f"● {dt['lead_count']} leads"
        print(f"  database       {_badge(leads_label, 'ok')}", end="")
        if dt.get("last_hunt_at"):
            print(f"  {dim('last hunt: ' + str(dt['last_hunt_at'])[:19])}")
        else:
            print(f"  {dim('no hunts yet')}")

    # Outreach pipeline state — appears between core status and
    # filesystem so it sits next to the most-actionable info.
    seq = report.get("sequence", {}) or {}
    inbox_state = report.get("inbox", {}) or {}
    sched = report.get("schedule", {}) or {}
    if any((seq, inbox_state, sched)):
        print()
        if seq and not seq.get("error"):
            opener = seq.get("step1_opener", 0)
            bump   = seq.get("step2_bump", 0)
            final  = seq.get("step3_final", 0)
            paused = seq.get("paused", 0)
            label = f"{opener} step1 / {bump} step2 / {final} step3"
            tone = "ok" if (opener or bump or final) else "info"
            print(f"  sequence       {_badge('● ' + label, tone)}", end="")
            if paused:
                print(f"  {dim(f'({paused} paused / replied)')}")
            else:
                print()
        if inbox_state:
            if inbox_state.get("configured"):
                host = inbox_state.get("host") or "configured"
                print(f"  inbox (IMAP)   {_badge('● ' + host, 'ok')}")
            else:
                print(f"  inbox (IMAP)   {_badge('○ not configured', 'info')}  "
                      f"{dim('huntova inbox setup')}")
        if sched:
            if sched.get("installed"):
                print(f"  daily schedule {_badge('● installed', 'ok')}  "
                      f"{dim(sched.get('path', ''))}")
            else:
                print(f"  daily schedule {_badge('○ not installed', 'info')}  "
                      f"{dim('huntova schedule print --target launchd')}")

    # Filesystem
    fs = report.get("filesystem", {})
    if fs:
        print()
        print(f"  {dim('config:')}    {fs.get('config', '?')}")
        print(f"  {dim('data:')}      {fs.get('data', '?')}")
        print(f"  {dim('secrets:')}   {fs.get('secrets', '?')}")

    # Telemetry
    if report.get("telemetry", {}).get("opted_in"):
        print(f"\n  {dim('telemetry:')} {_badge('● enabled', 'info')}")

    print()
    return 0


def _resolve_preferred_provider() -> str:
    """Read preferred_provider from config.toml. Returns 'anthropic' as
    fallback when unset (Huntova's default — see providers._DEFAULT_ORDER)."""
    try:
        import tomllib
        cfg_path = _config_dir() / "config.toml"
        if cfg_path.exists():
            data = tomllib.loads(cfg_path.read_text())
            return (data.get("preferred_provider") or "anthropic").strip().lower()
    except Exception:
        pass
    return "anthropic"


def _version_safe() -> str:
    try:
        return VERSION
    except NameError:
        return "?"


def cmd_config(args: argparse.Namespace) -> int:
    """Show / edit Huntova configuration.

    Subcommands:
      show         Pretty-print the config (secrets redacted)
      edit         Open ~/.config/huntova/config.toml in $EDITOR
      get <key>    Print a single value (e.g. `config get preferred_provider`)
      set <key> <value>  Write a single value
      path         Print the config file path
    """
    try:
        from tui import bold, dim, green, red, yellow, cyan
    except ImportError:
        bold = dim = green = red = yellow = cyan = lambda s: s

    sub = (args.subcommand or "show").lower()
    cfg_path = _config_dir() / "config.toml"

    if sub == "path":
        print(str(cfg_path))
        return 0

    if sub == "edit":
        editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")
        if not editor:
            # Pick a sensible default
            for candidate in ("nano", "vim", "vi", "code", "open"):
                import shutil
                if shutil.which(candidate):
                    editor = candidate
                    break
        if not editor:
            print("[huntova] $EDITOR not set and no editor found. "
                  "Set $EDITOR=nano (or your favorite) and re-run.", file=sys.stderr)
            return 1
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        if not cfg_path.exists():
            cfg_path.write_text(_default_config_template())
        try:
            import subprocess
            cmd = [editor]
            if editor == "code":
                cmd.append("--wait")
            cmd.append(str(cfg_path))
            return subprocess.call(cmd)
        except Exception as e:
            print(f"[huntova] couldn't launch {editor}: {e}", file=sys.stderr)
            return 1

    if sub == "show":
        if not cfg_path.exists():
            print()
            print(f"  {dim('config:')} {cfg_path} {dim('(not yet created)')}")
            print()
            print(f"  Run {bold('huntova onboard')} to set up your first provider —")
            print(f"  config.toml is created on demand by the wizard.")
            print()
            return 0
        try:
            import tomllib
            data = tomllib.loads(cfg_path.read_text())
        except Exception as e:
            print(f"[huntova] couldn't parse {cfg_path}: {e}", file=sys.stderr)
            return 1
        # Redact anything that looks like a secret
        SECRET_KEYS = {"api_key", "key", "token", "password", "secret",
                        "smtp_password", "webhook_url"}
        def _redact(d):
            if isinstance(d, dict):
                return {k: ("***redacted***" if any(s in k.lower() for s in SECRET_KEYS)
                            else _redact(v)) for k, v in d.items()}
            if isinstance(d, list):
                return [_redact(x) for x in d]
            return d
        import json as _json
        print()
        print(f"  {bold('config:')} {dim(str(cfg_path))}")
        print()
        for line in _json.dumps(_redact(data), indent=2, default=str).splitlines():
            print(f"  {line}")
        print()
        return 0

    if sub == "get":
        key = (args.key or "").strip()
        if not key:
            print("[huntova] usage: huntova config get <key>", file=sys.stderr)
            return 1
        try:
            import tomllib
            data = tomllib.loads(cfg_path.read_text()) if cfg_path.exists() else {}
        except Exception as e:
            print(f"[huntova] {e}", file=sys.stderr)
            return 1
        # Support dotted keys: providers.gemini_key
        cur: Any = data
        for part in key.split("."):
            if not isinstance(cur, dict) or part not in cur:
                print(f"[huntova] {key!r} not set.")
                return 1
            cur = cur[part]
        if any(s in key.lower() for s in ("password", "secret", "key")) and len(str(cur)) > 6:
            print("***redacted*** (read keychain via secrets_store CLI)")
        else:
            print(cur)
        return 0

    if sub == "set":
        key = (args.key or "").strip()
        value = args.value or ""
        if not key:
            print("[huntova] usage: huntova config set <key> <value>", file=sys.stderr)
            return 1
        # Refuse to set anything that looks like a secret — those go in keychain
        if any(s in key.lower() for s in ("password", "secret", "_key")) and "_provider" not in key:
            print(f"[huntova] {key!r} looks like a secret — store in keychain via `huntova onboard` instead.", file=sys.stderr)
            return 1
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        existing = cfg_path.read_text() if cfg_path.exists() else ""
        # Replace or append the line
        out_lines = []
        replaced = False
        for line in existing.splitlines():
            if line.strip().startswith(f"{key.split('.')[-1]} =") or line.strip().startswith(f"{key} ="):
                out_lines.append(f'{key} = "{value}"')
                replaced = True
            else:
                out_lines.append(line)
        if not replaced:
            out_lines.append(f'{key} = "{value}"')
        cfg_path.write_text("\n".join(out_lines) + "\n")
        if os.name != "nt":
            try: os.chmod(cfg_path, 0o600)
            except OSError: pass
        print(f"  {green('✓')} {key} = {value!r}")
        return 0

    if sub == "unset":
        # Delete a key from config.toml.
        # Secret-looking keys are blocked (they live in the keychain;
        # use `huntova onboard --reset-scope keys` for those).
        key = (args.key or "").strip()
        if not key:
            print("[huntova] usage: huntova config unset <key>", file=sys.stderr)
            return 1
        if any(s in key.lower() for s in ("password", "secret", "_key")) and "_provider" not in key:
            print(f"[huntova] {key!r} looks like a secret — keychain entries don't live in config.toml.\n"
                  f"           Run: huntova onboard --reset-scope keys", file=sys.stderr)
            return 1
        if not cfg_path.exists():
            print(f"[huntova] no config at {cfg_path} — nothing to unset.")
            return 0
        existing = cfg_path.read_text()
        kept: list[str] = []
        removed = False
        for line in existing.splitlines():
            stripped = line.strip()
            if stripped.startswith(f"{key.split('.')[-1]} =") or stripped.startswith(f"{key} ="):
                # Multi-line array / inline-table safety. A line ending
                # with `[` or `{` and no balancing close means the value
                # spans subsequent lines. We don't want to drop only the
                # opening line and leave orphaned `"DE",` lines that
                # break TOML parsing on the next `huntova` invocation.
                opens = stripped.count("[") + stripped.count("{")
                closes = stripped.count("]") + stripped.count("}")
                if opens > closes:
                    print(f"[huntova] {key!r} is a multi-line value (array or inline table).", file=sys.stderr)
                    print("           Refusing to delete partially. Edit the file directly:", file=sys.stderr)
                    print(f"             huntova config edit", file=sys.stderr)
                    return 1
                removed = True
                continue
            kept.append(line)
        if not removed:
            print(f"[huntova] {key!r} not present in config.toml.")
            return 0
        cfg_path.write_text("\n".join(kept) + ("\n" if kept else ""))
        print(f"  {green('✓')} removed {key}")
        return 0

    if sub == "validate":
        # Sanity-check the config — TOML parse, known-key allowlist,
        # type checks.
        if not cfg_path.exists():
            print(f"[huntova] no config at {cfg_path} — nothing to validate.")
            return 0
        try:
            import tomllib
            data = tomllib.loads(cfg_path.read_text())
        except Exception as e:
            print(f"  {red('✗')} TOML parse failed: {type(e).__name__}: {e}")
            return 1
        problems: list[str] = []
        # Top-level keys we know about
        known_top = {
            "preferred_provider", "hunting", "outreach", "csv_sink", "slack_ping",
            "dedup", "webhook", "telemetry",
        }
        for k in data.keys():
            if k.startswith("HV_"):
                continue  # env-style overrides are fine
            if k not in known_top:
                problems.append(f"unknown top-level key: {k!r}")
        # preferred_provider type
        pp = data.get("preferred_provider")
        if pp is not None and not isinstance(pp, str):
            problems.append(f"preferred_provider must be a string (got {type(pp).__name__})")
        # hunting.default_countries shape
        hunting = data.get("hunting") or {}
        if hunting:
            countries = hunting.get("default_countries")
            if countries is not None and not (isinstance(countries, list) and all(isinstance(c, str) for c in countries)):
                problems.append("hunting.default_countries must be a list of strings")
            mlph = hunting.get("max_leads_per_hunt")
            if mlph is not None and (not isinstance(mlph, int) or mlph < 1 or mlph > 500):
                problems.append("hunting.max_leads_per_hunt must be an integer in [1, 500]")
        if problems:
            print(f"  {red('✗')} {len(problems)} issue(s):")
            for p in problems:
                print(f"     · {p}")
            return 1
        print(f"  {green('✓')} {cfg_path} parses + matches schema")
        return 0

    print(f"[huntova] unknown config subcommand {sub!r} — try show / edit / get / set / unset / validate / path", file=sys.stderr)
    return 1


def _default_config_template() -> str:
    return """# Huntova configuration
# Non-secret settings only. API keys live in your OS keychain
# (or ~/.config/huntova/secrets.enc fallback).

# AI provider used for scoring + email drafting
preferred_provider = "anthropic"

# Hunting defaults
[hunting]
default_countries = ["USA", "United Kingdom", "Germany", "France"]
max_leads_per_hunt = 25
playwright_deep_qualify = true

# Outreach (cold-email send) — credentials in keychain, not here
[outreach]
daily_send_cap = 50
default_dry_run = true   # require --no-dry-run to actually send

# Workspace
[workspace]
name = "default"

# Optional proxy (for SearXNG / web fetches)
[proxy]
http = ""
https = ""

# Telemetry — opt-in. See `huntova telemetry enable`.
"""


def cmd_test_integrations(args: argparse.Namespace) -> int:
    """Probe every configured integration end-to-end.

    For each: AI providers, SearXNG, Playwright, plugins, SMTP (if
    configured). Returns non-zero if any critical integration fails.

    Examples:
        huntova test-integrations
        huntova test-integrations --json
    """
    try:
        from tui import bold, dim, green, red, yellow, cyan, with_spinner
    except ImportError:
        bold = dim = green = red = yellow = cyan = lambda s: s
        def with_spinner(label, fn):
            try: return True, fn()
            except Exception as e: return False, e

    results: list[dict] = []

    # 1. AI providers — probe each configured one
    try:
        from providers import list_available_providers, get_provider
        configured = list_available_providers() or []
    except Exception as e:
        configured = []
        results.append({"name": "providers.list", "ok": False, "required": True,
                          "msg": str(e)[:80]})
    if not configured:
        print(f"  {dim('▸')} AI providers: {dim('skipped')} {dim('(none configured — run `huntova onboard`)')}")
        results.append({"name": "ai", "ok": True, "skipped": True,
                          "msg": "no provider configured"})
    for slug in configured:
        ok, val = with_spinner(
            f"AI provider: {slug}",
            lambda s=slug: get_provider({"preferred_provider": s}).chat(
                messages=[{"role": "user", "content": "respond with OK"}],
                max_tokens=5, temperature=0.0, timeout_s=15.0,
            ),
        )
        results.append({"name": f"ai.{slug}", "ok": ok,
                          "msg": (val if ok else type(val).__name__)[:60]})

    # 2. SearXNG — skip if SEARXNG_URL is unset (don't fail a vanilla
    # install with no SearXNG running). When user explicitly sets the
    # env var, fail-on-broken is the correct policy.
    searxng_env = os.environ.get("SEARXNG_URL", "").strip()
    if not searxng_env:
        print(f"  {dim('▸')} SearXNG: {dim('skipped')} {dim('(SEARXNG_URL not set)')}")
        results.append({"name": "searxng", "ok": True, "skipped": True,
                          "msg": "SEARXNG_URL not configured"})
    else:
        def _searxng_probe():
            import urllib.request, json as _j
            url = searxng_env.rstrip("/") + "/search?q=huntova_test&format=json"
            with urllib.request.urlopen(url, timeout=4) as r:
                data = _j.loads(r.read())
            return f"reachable, {len(data.get('results', []))} results"
        ok, val = with_spinner(f"SearXNG: {searxng_env}", _searxng_probe)
        results.append({"name": "searxng", "ok": ok, "msg": (val if ok else type(val).__name__)[:60]})

    # 3. Playwright — skip if not installed (it's optional; agent runs
    # in requests-only mode when missing). Fail only if installed but
    # the Chromium browser binary is missing.
    from importlib.util import find_spec as _find_spec
    if not _find_spec("playwright"):
        print(f"  {dim('▸')} Playwright: {dim('skipped')} {dim('(not installed — agent will use requests-only mode)')}")
        results.append({"name": "playwright", "ok": True, "skipped": True,
                          "msg": "playwright not installed"})
    else:
        def _playwright_probe():
            import subprocess
            out = subprocess.run(
                [sys.executable, "-c",
                 "from playwright.sync_api import sync_playwright;"
                 "p = sync_playwright().start();"
                 "_=p.chromium.executable_path; p.stop()"],
                capture_output=True, text=True, timeout=10,
            )
            if out.returncode != 0:
                raise RuntimeError("Chromium browser missing — run `playwright install chromium`")
            return "ready"
        ok, val = with_spinner("Playwright + Chromium", _playwright_probe)
        results.append({"name": "playwright", "ok": ok, "msg": (val if ok else str(val))[:80]})

    # 4. Plugins
    def _plugins_probe():
        from plugins import get_registry, reset_for_tests
        try: reset_for_tests()
        except Exception: pass
        reg = get_registry()
        reg.discover()
        names = [p.get("name") if isinstance(p, dict) else getattr(p, "name", None)
                 for p in reg.list_plugins()]
        errs = reg.errors() or []
        if errs:
            raise RuntimeError(f"{len(errs)} plugin(s) failed: " +
                                "; ".join(f"{s}: {str(e)[:30]}" for s, e in errs[:3]))
        return f"{len(names)} loaded"
    ok, val = with_spinner("Plugin discovery", _plugins_probe)
    results.append({"name": "plugins", "ok": ok, "msg": (val if ok else str(val))[:80]})

    # 5. SMTP (if configured) — HELO probe, no actual send
    smtp_host = os.environ.get("SMTP_HOST")
    if smtp_host:
        def _smtp_probe():
            import smtplib
            with smtplib.SMTP(smtp_host, int(os.environ.get("SMTP_PORT") or 587),
                                timeout=8) as s:
                s.starttls()
                if os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASSWORD"):
                    s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
            return "auth ok"
        ok, val = with_spinner(f"SMTP: {smtp_host}", _smtp_probe)
        results.append({"name": "smtp", "ok": ok,
                          "msg": (val if ok else type(val).__name__)[:80]})
    else:
        print(f"  {dim('▸')} SMTP: {dim('skipped')} {dim('(SMTP_HOST not set — outreach send will be unavailable)')}")
        results.append({"name": "smtp", "ok": True, "skipped": True,
                          "msg": "SMTP_HOST not configured"})

    if args.format == "json":
        import json as _json
        print(_json.dumps({"results": results,
                            "ok_count": sum(1 for r in results if r["ok"] and not r.get("skipped")),
                            "fail_count": sum(1 for r in results if not r["ok"]),
                            "skipped_count": sum(1 for r in results if r.get("skipped"))},
                           indent=2, default=str))
        # Only fail on hard errors, not skipped-because-unconfigured
        return 1 if any(not r["ok"] for r in results) else 0

    # Pretty summary
    print()
    print(f"  {bold('Integration test results:')}")
    ok_count = sum(1 for r in results if r["ok"] and not r.get("skipped"))
    skip_count = sum(1 for r in results if r.get("skipped"))
    fail_count = sum(1 for r in results if not r["ok"])
    total_run = len(results) - skip_count
    if fail_count == 0 and skip_count == 0:
        print(f"  {green('●')} all {len(results)} integrations passed")
    elif fail_count == 0:
        print(f"  {green('●')} {ok_count}/{total_run} configured integrations passed "
              f"{dim(f'({skip_count} skipped)')}")
    else:
        print(f"  {yellow('●')} {ok_count}/{total_run} configured integrations passed, "
              f"{red(str(fail_count))} failed {dim(f'({skip_count} skipped)') if skip_count else ''}")
    print()
    # Only return non-zero when a CONFIGURED integration actually failed.
    # A vanilla install with no providers + no SearXNG yet should not
    # exit red — those are skips, not failures.
    return 1 if fail_count else 0


def cmd_daemon(args: argparse.Namespace) -> int:
    """Install / control the Huntova background daemon.

    On macOS: writes ~/Library/LaunchAgents/com.huntova.gateway.plist
    On Linux: writes ~/.config/systemd/user/huntova.service
    On Windows: not supported yet (use Task Scheduler manually).

    Subcommands:
      install   Write the unit file + load it (auto-runs on login)
      uninstall Stop + remove the unit file
      start     Start the daemon (idempotent if already running)
      stop      Stop without uninstalling
      status    Show running / stopped / not-installed
      logs      Tail the daemon logs (~/.local/share/huntova/logs/)

    Examples:
        huntova daemon install
        huntova daemon status
        huntova daemon logs
    """
    sub = (args.subcommand or "status").lower()
    try:
        from tui import bold, dim, green, red, yellow, cyan, purple
    except ImportError:
        bold = dim = green = red = yellow = cyan = purple = lambda s: s
    import huntova_daemon as _daemon

    if sub == "install":
        port = int(args.port or DEFAULT_PORT)
        # Pass through environment vars the user may want the daemon
        # to inherit. Best-effort — the keychain is the canonical source
        # for keys, env-vars are an override.
        passthrough_env = {
            k: v for k, v in os.environ.items()
            if k.startswith("HV_") and not k.endswith("_KEY")  # secrets via keychain only
        }
        passthrough_env["APP_MODE"] = "local"
        print(f"\n  {cyan('▸')} Installing daemon on port {bold(str(port))}…")
        ok, result = _daemon.install_daemon(port=port, environment=passthrough_env)
        if not ok:
            print(f"  {red('✗')} {result}")
            return 1
        print(f"  {green('✓')} unit installed: {dim(result)}")
        print(f"  {green('✓')} loaded + running on http://127.0.0.1:{port}")
        print()
        print(f"  Tail logs:    {cyan('huntova daemon logs')}")
        print(f"  Status:       {cyan('huntova daemon status')}")
        print(f"  Uninstall:    {cyan('huntova daemon uninstall')}")
        print()
        return 0

    if sub == "uninstall":
        print(f"\n  {cyan('▸')} Uninstalling Huntova daemon…")
        ok, result = _daemon.uninstall_daemon()
        if ok:
            print(f"  {green('✓')} {result}")
            return 0
        print(f"  {red('✗')} {result}")
        return 1

    if sub == "start":
        ok, result = _daemon.start_daemon()
        marker = green('✓') if ok else red('✗')
        print(f"  {marker} {result}")
        return 0 if ok else 1

    if sub == "stop":
        ok, result = _daemon.stop_daemon()
        marker = green('✓') if ok else red('✗')
        print(f"  {marker} {result}")
        return 0 if ok else 1

    if sub == "status":
        status = _daemon.daemon_status()
        colour = {"running": green, "stopped": yellow, "not-installed": dim,
                  "unknown": yellow, "unsupported": red}.get(status, dim)
        print(f"\n  Huntova daemon: {colour(status)}")
        if status == "not-installed":
            print(f"  Install with:   {cyan('huntova daemon install')}")
        elif status == "stopped":
            print(f"  Start with:     {cyan('huntova daemon start')}")
        elif status == "running":
            print(f"  Dashboard:      {cyan('http://127.0.0.1:' + str(DEFAULT_PORT))}")
        print()
        return 0

    if sub == "logs":
        from pathlib import Path
        log_dir = Path(os.environ.get("XDG_DATA_HOME") or
                       (Path.home() / ".local" / "share")) / "huntova" / "logs"
        out_log = log_dir / "daemon.out"
        err_log = log_dir / "daemon.err"
        if not out_log.exists() and not err_log.exists():
            print(f"  {dim('no daemon logs yet — run `huntova daemon install` first')}")
            return 0
        if args.follow:
            import subprocess
            files = [str(p) for p in (out_log, err_log) if p.exists()]
            try:
                subprocess.call(["tail", "-f"] + files)
            except KeyboardInterrupt:
                pass
            return 0
        for log in (out_log, err_log):
            if log.exists():
                print(f"\n  {bold(str(log))}")
                print(f"  {dim('─' * (len(str(log)) + 2))}")
                try:
                    text = log.read_text(errors="ignore")
                except Exception as e:
                    print(f"  {red('✗')} {e}")
                    continue
                tail = text.splitlines()[-50:]
                for line in tail:
                    print(f"    {line}")
        return 0

    print(f"[huntova] unknown daemon subcommand {sub!r} — try install/uninstall/start/stop/status/logs", file=sys.stderr)
    return 1


def cmd_onboard(args: argparse.Namespace) -> int:
    """First-run wizard — Huntova's flagship setup experience.

    Three phases:
      1. Filesystem — verify config / data / secrets backend
      2. Provider — pick from 13 options (cloud / local / custom),
         enter key (hidden input) or auto-detect a local AI server,
         live AI probe
      3. Launch — open the dashboard, suggest the 60-second pipeline

    Polish: arrow-key select via questionary, animated spinners,
    box-drawn ASCII banner, SSH/WSL/DISPLAY-aware browser launch,
    cancel handling on Ctrl+C.

    Examples:
        huntova onboard
        huntova onboard --browser           # skip TUI, open the web wizard
        huntova onboard --no-launch         # don't auto-start `huntova serve` at the end
        huntova onboard --no-prompt         # CI / scripted (uses env keys)
        huntova onboard --reset-scope keys  # wipe keychain entries before re-running
    """
    # Reset before the wizard runs — drop the chosen scope of state
    # (config / keychain / cache / db) so re-onboarding is clean.
    if getattr(args, "reset_scope", None):
        _apply_reset_scope(args.reset_scope)

    # Per-provider keys passed via flags — save them to the keychain up
    # front so the wizard sees them as already-configured. This is what
    # makes scripted/CI onboarding work end-to-end without prompts:
    # `huntova onboard --no-prompt --accept-risk --gemini-api-key sk-...`
    _apply_provider_flags(args)

    # Safety gate: --no-prompt requires --accept-risk so users running
    # scripted setups make a deliberate ack of the autonomy + network-
    # access surface area.
    if getattr(args, "no_prompt", False) and not getattr(args, "accept_risk", False):
        # Check if any keys were actually supplied — if so, the user is
        # clearly scripted; nudge them to add --accept-risk explicitly.
        any_key = any(
            getattr(args, f"key_{slug}", None)
            for slug in ("gemini", "anthropic", "openai", "openrouter",
                         "groq", "deepseek", "together", "mistral",
                         "perplexity", "ollama", "lmstudio", "llamafile",
                         "custom")
        )
        if any_key:
            print(
                "[huntova] --no-prompt requires --accept-risk to acknowledge "
                "the agent has full network access on your machine.",
                file=sys.stderr,
            )
            print(
                "          Re-run with: huntova onboard --no-prompt --accept-risk ...",
                file=sys.stderr,
            )
            return 1

    result = _onboard_v2(args)

    # JSON summary mode — for scripted setups that want to verify state.
    if getattr(args, "json", False):
        import json as _json
        try:
            from providers import list_available_providers
            avail = list_available_providers() or []
        except Exception:
            avail = []
        summary = {
            "ok": result == 0,
            "exit_code": result,
            "providers_configured": avail,
            "config_path": str(_config_dir() / "config.toml"),
            "version": VERSION,
        }
        print(_json.dumps(summary, indent=2))
    return result


def _apply_provider_flags(args: argparse.Namespace) -> None:
    """Save per-provider keys passed via CLI flags to the keychain.

    Maps each `--<slug>-api-key` flag to its HV_*_KEY env var and writes
    via secrets_store.set_secret. Best-effort — failures are surfaced but
    don't abort the wizard (the user can re-enter interactively).
    """
    flag_to_env = {
        "key_gemini":     "HV_GEMINI_KEY",
        "key_anthropic":  "HV_ANTHROPIC_KEY",
        "key_openai":     "HV_OPENAI_KEY",
        "key_openrouter": "HV_OPENROUTER_KEY",
        "key_groq":       "HV_GROQ_KEY",
        "key_deepseek":   "HV_DEEPSEEK_KEY",
        "key_together":   "HV_TOGETHER_KEY",
        "key_mistral":    "HV_MISTRAL_KEY",
        "key_perplexity": "HV_PERPLEXITY_KEY",
        "key_ollama":     "HV_OLLAMA_KEY",
        "key_lmstudio":   "HV_LMSTUDIO_KEY",
        "key_llamafile":  "HV_LLAMAFILE_KEY",
        "key_custom":     "HV_CUSTOM_KEY",
        "custom_base_url": "HV_CUSTOM_BASE_URL",
        "custom_model":    "HV_CUSTOM_MODEL",
    }
    saved: list[str] = []
    try:
        from secrets_store import set_secret
    except Exception as e:
        print(f"[huntova] couldn't load secrets_store: {e}", file=sys.stderr)
        return
    for attr, env_var in flag_to_env.items():
        val = getattr(args, attr, None)
        if val:
            try:
                set_secret(env_var, val)
                os.environ[env_var] = val
                saved.append(env_var)
            except Exception as e:
                print(f"[huntova] couldn't save {env_var}: {type(e).__name__}: {e}", file=sys.stderr)
    if saved:
        # Goes to stderr so `huntova onboard --json` keeps stdout
        # clean for the JSON summary that `cmd_onboard` writes.
        print(f"[huntova] saved {len(saved)} key(s) from flags: {', '.join(saved)}", file=sys.stderr)


def _apply_reset_scope(scope: str) -> None:
    """Wipe local state before re-running the wizard.

    scope:
      "config" — erase config.toml only
      "keys"   — erase config.toml + keychain entries (every HV_*_KEY
                 plus HV_CUSTOM_BASE_URL/MODEL)
      "full"   — erase config + keychain + local SQLite DB

    A small union type used by the onboard --reset-scope flag.
    Confirms with the user before touching anything.
    """
    try:
        from tui import bold, dim, red, yellow, cyan, confirm
    except Exception:
        bold = dim = red = yellow = cyan = lambda s: s
        def confirm(msg, default=False):
            ans = input(f"{msg} [y/N]: ").strip().lower()
            return ans in ("y", "yes")
    print()
    print(f"  {yellow('!')} About to reset Huntova state — scope={bold(scope)}")
    print(f"  {dim('Wiping:')}")
    print(f"  {dim('  ·')} config.toml at ~/.config/huntova/config.toml")
    if scope in ("keys", "full"):
        print(f"  {dim('  ·')} OS keychain entries (every HV_*_KEY plus HV_CUSTOM_*)")
    if scope == "full":
        print(f"  {dim('  ·')} local DB at ~/.local/share/huntova/db.sqlite (leads + history)")
    print()
    if not confirm("Proceed with reset?", default=False):
        print(f"  {dim('aborted — no state was modified')}")
        sys.exit(0)
    # 1. config.toml
    cfg_path = _config_dir() / "config.toml"
    if cfg_path.exists():
        try:
            cfg_path.unlink()
            print(f"    {cyan('▸')} removed {dim(str(cfg_path))}")
        except OSError as e:
            print(f"    {red('✗')} couldn't remove {cfg_path}: {e}")
    # 2. keychain
    if scope in ("keys", "full"):
        try:
            from secrets_store import delete_secret
            from providers import _ENV_KEY as _PROVIDER_ENV
            for env_var in list(_PROVIDER_ENV.values()) + ["HV_CUSTOM_BASE_URL", "HV_CUSTOM_MODEL"]:
                try:
                    delete_secret(env_var)
                except Exception:
                    pass
                # Also unset in the current process so subsequent calls
                # in the same `huntova` invocation don't keep using the
                # stale value cached in os.environ.
                os.environ.pop(env_var, None)
            print(f"    {cyan('▸')} cleared keychain entries")
        except Exception as e:
            print(f"    {red('✗')} keychain reset failed: {type(e).__name__}: {e}")
    # 3. local DB
    if scope == "full":
        try:
            import db_driver as _dbd
            db_path = _dbd._local_db_path()
            if db_path.exists():
                db_path.unlink()
                print(f"    {cyan('▸')} removed {dim(str(db_path))}")
        except Exception as e:
            print(f"    {red('✗')} couldn't remove local DB: {type(e).__name__}: {e}")
    print()


def _onboard_v2(args: argparse.Namespace) -> int:
    """The richer onboard implementation backed by the tui module."""
    try:
        from tui import (
            print_banner, intro, outro, note, cancelled,
            select, password, confirm, with_spinner,
            SelectOption, bold, dim, cyan, green, red, yellow, purple,
            open_url, detect_browser_open_support,
        )
    except Exception as e:
        print()
        print("  Rich onboarding UI is unavailable on this terminal — running")
        print(f"  the lightweight setup wizard instead. ({type(e).__name__})")
        print()
        print("  Tip: install the polish dependency to unlock the full TUI:")
        print("       pipx inject huntova questionary")
        print()
        return _onboard_v1(args)

    # ── Browser shortcut ─────────────────────────────────────────
    if args.browser:
        port = int(os.environ.get("HV_PORT") or DEFAULT_PORT)
        url = f"http://127.0.0.1:{port}/setup"
        print_banner("First-run setup · web wizard")
        print(f"  {cyan('▸')} Starting `huntova serve` on port {port}")
        print(f"  {cyan('▸')} Will open the browser at {bold(url)}")
        print()
        # Hand off to cmd_serve. Use SimpleNamespace to avoid the class-body
        # scope rule — `class X: port = port` reads `port` from the class
        # namespace (which is empty), not the enclosing function, raising
        # NameError. SimpleNamespace builds the attrs at call time from the
        # surrounding scope.
        from types import SimpleNamespace
        return cmd_serve(SimpleNamespace(host=None, port=port,
                                          no_browser=False, setup_first=True))

    # ── Banner ────────────────────────────────────────────────────
    print_banner("First-run setup · ~60 seconds")

    # ── Existing-config card ─────────────────────────────────────
    # Show what's already on disk so returning users see "yes I
    # remember you" before being asked any questions. Skipped on
    # first-run since there's nothing to summarise yet.
    cfg_path = _config_dir() / "config.toml"
    if cfg_path.exists():
        try:
            from tui import config_summary_card
            import tomllib
            existing_cfg = tomllib.loads(cfg_path.read_text())
            from providers import list_available_providers as _lap
            avail = _lap() or []
            items: list[tuple[str, str]] = [
                ("config", str(cfg_path)),
                ("preferred_provider", str(existing_cfg.get("preferred_provider") or "anthropic")),
                ("providers configured", ", ".join(avail) if avail else "(none yet)"),
            ]
            try:
                from secrets_store import _backend_label as _bl
                items.append(("secrets backend", _bl()))
            except Exception:
                pass
            config_summary_card(items)
        except Exception:
            pass

    # ── Step 1: Filesystem ────────────────────────────────────────
    intro("Step 1 of 3 — Filesystem")
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    if not cfg_path.exists():
        cfg_path.write_text("# Huntova config\npreferred_provider = \"anthropic\"\n")
        if os.name != "nt":
            try: os.chmod(cfg_path, 0o600)
            except OSError: pass
    print(f"    {green('✓')} config dir   {dim(str(cfg_path.parent))}")
    try:
        import db_driver as _dbd
        db_path = _dbd._local_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # If the SQLite file already exists (e.g. left over from a
        # previous run), tighten its permissions to 0600 so a brand-new
        # `huntova security audit` doesn't immediately flag the
        # vanilla install.
        if db_path.exists() and os.name != "nt":
            try: os.chmod(db_path, 0o600)
            except OSError: pass
        print(f"    {green('✓')} data dir     {dim(str(db_path.parent))}")
    except Exception as e:
        print(f"    {red('✗')} data dir     {type(e).__name__}: {e}")
    try:
        from secrets_store import _backend_label, set_secret, get_secret
        backend = _backend_label()
        backend_color = green if backend == "keyring" else yellow
        print(f"    {backend_color('✓')} secrets      {dim(backend)}")
        # Auto-generate HV_SECRET_KEY on first run so the security
        # audit doesn't immediately fire on a fresh install. Persisted
        # to the keychain — same surface as API keys, so the user
        # never sees it but every subsequent boot picks it up via
        # _hydrate_env_from_local_config.
        if not os.environ.get("HV_SECRET_KEY") and not get_secret("HV_SECRET_KEY"):
            import secrets as _secrets
            generated = _secrets.token_urlsafe(48)
            # ALWAYS set os.environ so the current run has a key, even
            # when keychain write fails. Otherwise the bare except
            # would swallow the persist failure and the next CLI run
            # would regenerate a fresh key, invalidating every signed
            # session/cookie. Surfaces a stderr line so the user can
            # see why their key didn't persist.
            os.environ["HV_SECRET_KEY"] = generated
            try:
                set_secret("HV_SECRET_KEY", generated)
                print(f"    {green('✓')} secret key   {dim('auto-generated and saved to keychain')}")
            except Exception as e:
                print(f"    {yellow('!')} secret key   {dim(f'generated for this run, but keychain write failed: {type(e).__name__}')}")
                print(f"    {dim('               run `export HV_SECRET_KEY=...` in your shell to persist')}",
                      file=sys.stderr)
    except Exception as e:
        print(f"    {red('✗')} secrets      {type(e).__name__}")

    # ── Step 2: Provider + key ────────────────────────────────────
    print()
    intro("Step 2 of 3 — Provider")
    if not sys.stdin.isatty() or args.no_prompt:
        print(f"    {yellow('▸')} non-interactive mode — skipping provider prompt.")
        print(f"    Set ONE of these in your env, then run {cyan('huntova hunt')}:")
        for env_var in ("HV_GEMINI_KEY", "HV_ANTHROPIC_KEY", "HV_OPENAI_KEY",
                        "HV_OPENROUTER_KEY", "HV_GROQ_KEY", "HV_DEEPSEEK_KEY",
                        "HV_OLLAMA_KEY (local — see https://ollama.com)"):
            print(f"      {dim('export')} {env_var}=…")
        print()
        return 0

    # Auto-detect local AI servers running on localhost
    print(f"    {dim('Probing localhost for running AI servers…')}")
    local_servers = {}
    try:
        from providers import detect_local_servers
        local_servers = detect_local_servers()
    except Exception:
        pass
    detected_local = [name for name, info in local_servers.items()
                      if info.get("available")]
    if detected_local:
        print(f"    {green('●')} detected: {', '.join(detected_local)}")
    else:
        print(f"    {dim('●')} no local AI servers running (Ollama, LM Studio, llamafile)")
    print()

    # Build the provider menu — local-detected first (free!), then cloud
    options: list = []
    if "ollama" in detected_local:
        options.append(SelectOption("ollama", "🦙 Ollama (detected, free, local)",
                                     f"{local_servers['ollama'].get('model_count', 0)} models loaded"))
    if "lmstudio" in detected_local:
        options.append(SelectOption("lmstudio", "🎛️  LM Studio (detected, free, local)",
                                     f"{local_servers['lmstudio'].get('model_count', 0)} models loaded"))
    if "llamafile" in detected_local:
        options.append(SelectOption("llamafile", "📦 llamafile (detected, free, local)", ""))
    options.extend([
        SelectOption("anthropic",  "🟧 Anthropic Claude",  "highest accuracy · ~$0.04 / 10 leads · default"),
        SelectOption("gemini",     "🟦 Google Gemini",     "free tier · ~$0.005 / 10 leads"),
        SelectOption("openai",     "⚪ OpenAI GPT",        "broad model selection · ~$0.015 / 10 leads"),
        SelectOption("openrouter", "🔀 OpenRouter",        "200+ models · 1 API key · pay per call"),
        SelectOption("groq",       "⚡ Groq",              "Llama 3.3 70B at 750 tok/s · cheap fast"),
        SelectOption("deepseek",   "🐋 DeepSeek",          "~$0.001 / 10 leads · cheapest reasoning"),
        SelectOption("together",   "🤝 Together AI",       "200+ open-weight models"),
        SelectOption("mistral",    "🇫🇷 Mistral",         "EU-hosted · GDPR-friendly"),
        SelectOption("perplexity", "🔍 Perplexity",        "web-grounded sonar models"),
    ])
    # Local options not detected — show as "offline" but still pickable
    if "ollama" not in detected_local:
        options.append(SelectOption("ollama", "🦙 Ollama (not running)",
                                     "install: curl https://ollama.com/install.sh | sh"))
    if "lmstudio" not in detected_local:
        options.append(SelectOption("lmstudio", "🎛️  LM Studio (not running)",
                                     "install: lmstudio.ai"))
    options.append(SelectOption("custom", "⚙️  Custom OpenAI-compatible endpoint",
                                 "LiteLLM gateway · self-hosted vLLM · enterprise relay"))

    chosen = select("Pick a provider:", options, default="anthropic")
    if chosen is None:
        cancelled()
        return 0

    is_local = chosen in ("ollama", "lmstudio", "llamafile")
    is_custom = chosen == "custom"

    # ── Custom-endpoint extra fields ──
    base_url = ""
    custom_model = ""
    if is_custom:
        from tui import text as _text_prompt
        base_url = _text_prompt(
            "Base URL (e.g. https://my-gateway.example.com/v1):",
            placeholder="https://my-gateway.example.com/v1",
            validate=lambda v: None if v.startswith(("http://", "https://")) else "must start with http:// or https://",
        )
        if base_url is None:
            cancelled()
            return 0
        custom_model = _text_prompt(
            "Model name (e.g. gpt-4o-mini):",
            placeholder="gpt-4o-mini",
        ) or "custom-model"

    # ── Key prompt (skipped for local-no-key) ──
    api_key = ""
    if is_local:
        if confirm("Local server has no auth (default). Skip key entry?", default=True):
            api_key = ""  # save sentinel "no-key" later
        else:
            api_key = password("Optional auth token:") or ""
    else:
        env_var_map = {
            "gemini": "HV_GEMINI_KEY", "anthropic": "HV_ANTHROPIC_KEY",
            "openai": "HV_OPENAI_KEY", "openrouter": "HV_OPENROUTER_KEY",
            "groq": "HV_GROQ_KEY", "deepseek": "HV_DEEPSEEK_KEY",
            "together": "HV_TOGETHER_KEY", "mistral": "HV_MISTRAL_KEY",
            "perplexity": "HV_PERPLEXITY_KEY", "custom": "HV_CUSTOM_KEY",
        }
        env_var_for_chosen = env_var_map.get(chosen, "HV_KEY")
        existing_key = os.environ.get(env_var_for_chosen)
        if existing_key:
            note(f"Found {env_var_for_chosen} in your env. Re-use or replace?")
            if confirm("Use the existing key?", default=True):
                api_key = existing_key
        if not api_key:
            url_map = {
                "gemini": "https://aistudio.google.com/apikey",
                "anthropic": "https://console.anthropic.com/settings/keys",
                "openai": "https://platform.openai.com/api-keys",
                "openrouter": "https://openrouter.ai/keys",
                "groq": "https://console.groq.com/keys",
                "deepseek": "https://platform.deepseek.com/api_keys",
                "together": "https://api.together.xyz/settings/api-keys",
                "mistral": "https://console.mistral.ai/api-keys/",
                "perplexity": "https://www.perplexity.ai/settings/api",
                "custom": "",
            }
            if url_map.get(chosen):
                print(f"    {dim('Get a key at:')} {cyan(url_map[chosen])}")
            api_key = password(f"Paste your {chosen} API key (input hidden):",
                                validate=lambda v: None if len(v) >= 6 else "key looks too short")
            if api_key is None:
                cancelled()
                return 0

    # ── Save + probe ──
    env_var_map = {
        "gemini": "HV_GEMINI_KEY", "anthropic": "HV_ANTHROPIC_KEY",
        "openai": "HV_OPENAI_KEY", "openrouter": "HV_OPENROUTER_KEY",
        "groq": "HV_GROQ_KEY", "deepseek": "HV_DEEPSEEK_KEY",
        "together": "HV_TOGETHER_KEY", "mistral": "HV_MISTRAL_KEY",
        "perplexity": "HV_PERPLEXITY_KEY",
        "ollama": "HV_OLLAMA_KEY", "lmstudio": "HV_LMSTUDIO_KEY",
        "llamafile": "HV_LLAMAFILE_KEY", "custom": "HV_CUSTOM_KEY",
    }
    env_var = env_var_map[chosen]
    save_value = api_key or ("no-key" if (is_local or is_custom) else "")

    def _save():
        from secrets_store import set_secret
        if save_value:
            set_secret(env_var, save_value)
        if is_custom and base_url:
            set_secret("HV_CUSTOM_BASE_URL", base_url)
            os.environ["HV_CUSTOM_BASE_URL"] = base_url
            if custom_model:
                set_secret("HV_CUSTOM_MODEL", custom_model)
                os.environ["HV_CUSTOM_MODEL"] = custom_model
        # Persist preferred_provider in config.toml
        existing = cfg_path.read_text() if cfg_path.exists() else ""
        lines = [ln for ln in existing.splitlines() if not ln.strip().startswith("preferred_provider")]
        lines.insert(0, f'preferred_provider = "{chosen}"')
        cfg_path.write_text("\n".join(lines) + "\n")
        if os.name != "nt":
            try: os.chmod(cfg_path, 0o600)
            except OSError: pass
        return True

    print()
    ok_save, _ = with_spinner(f"saving to {dim(_backend_label_safe())}", _save)
    if not ok_save:
        return 1
    if save_value:
        os.environ[env_var] = save_value

    # Live probe
    def _probe():
        from providers import get_provider
        settings = {"preferred_provider": chosen, env_var: save_value or "no-key"}
        if is_custom:
            settings["HV_CUSTOM_BASE_URL"] = base_url
            if custom_model:
                settings["HV_CUSTOM_MODEL"] = custom_model
        p = get_provider(settings)
        resp = p.chat(
            messages=[{"role": "user", "content": "respond with OK"}],
            max_tokens=5, temperature=0.0, timeout_s=15.0,
        )
        return (resp or "").strip()[:30] or "(empty response)"

    ok_probe, probe_result = with_spinner(
        f"testing {chosen} with a live AI probe", _probe,
    )
    if not ok_probe:
        print(f"    {yellow('!')} probe failed but key saved — first hunt may surface the real error")
    else:
        print(f"    {dim('reply:')} {probe_result!r}")

    # ── Step 3: Launch ────────────────────────────────────────────
    print()
    intro("Step 3 of 3 — Launch")
    note(
        "Try the 60-second cold-email pipeline:\n\n"
        "  $ huntova examples install tech-recruiting\n"
        "  $ huntova recipe run tech-recruiting\n"
        "  $ huntova outreach send --top 5 --dry-run",
        title="Next steps",
    )
    print()
    if not args.no_launch:
        if confirm("Open the dashboard now?", default=True):
            class _Args:
                host = None; port = None; no_browser = False
            return cmd_serve(_Args())
    print()
    print(f"  {bold('Setup complete.')} {dim('Try one of these next:')}")
    print()
    _tips = [
        ("huntova serve",              "open the dashboard in your browser"),
        ("huntova hunt --max-leads 5", "find your first 5 leads in the terminal"),
        ("huntova chat",               "talk to the agent in natural language"),
        ("huntova examples ls",        "10 starter recipes for common ICPs"),
        ("huntova migrate from-csv",   "import existing leads from Apollo/Clay/etc"),
        ("huntova plugins ls",         "5 bundled plugins, browse community at /plugins"),
        ("huntova security audit",     "check your local install is locked down"),
        ("huntova doctor",             "full diagnostic any time something looks off"),
    ]
    _w = max(len(c) for c, _ in _tips)
    for _cmd, _desc in _tips:
        print(f"  {cyan(_cmd.ljust(_w))}  {dim('· ' + _desc)}")
    print()
    outro("Happy hunting.")
    metrics_emit("cli_onboard", {"completed": True, "provider": chosen,
                                   "is_local": is_local, "is_custom": is_custom})
    return 0


def _onboard_v1(args: argparse.Namespace) -> int:
    """Legacy onboard fallback (used when tui module fails to import).
    Original implementation pre-tui module — kept verbatim."""
    bold = lambda s: _ansi(s, "1")
    dim = lambda s: _ansi(s, "2")
    cyan = lambda s: _ansi(s, "36")
    green = lambda s: _ansi(s, "32")
    red = lambda s: _ansi(s, "31")
    purple = lambda s: _ansi(s, "35")
    yellow = lambda s: _ansi(s, "33")

    print()
    print(purple(_box_top(60, "Huntova onboard")))
    print(purple(_box_mid("First-run setup · ~60 seconds", 60)))
    print(purple(_box_bottom(60)))
    print()

    # Browser shortcut — skip the TUI, just open /setup
    if args.browser:
        port = int(os.environ.get("HV_PORT") or DEFAULT_PORT)
        print(f"  {cyan('▸')} Launching web wizard on http://127.0.0.1:{port}/setup")
        print(f"    {dim('(starting `huntova serve` in the background)')}")
        # Best-effort fork: spawn `huntova serve` in a subprocess and
        # open the browser. The subprocess inherits this process's
        # env, so any keys already set will be picked up.
        import subprocess
        try:
            # Spawn `huntova serve` in the background. Prefer the
            # installed binary (resolved via shutil.which) so the
            # detached process inherits the user's pipx venv. Falls
            # back to `python -m cli serve` if the binary isn't on
            # PATH (e.g. running from a development checkout).
            import shutil as _shutil
            huntova_bin = _shutil.which("huntova")
            if huntova_bin:
                cmd = [huntova_bin, "serve", "--port", str(port), "--no-browser"]
            else:
                cmd = [sys.executable, "-m", "cli", "serve",
                       "--port", str(port), "--no-browser"]
            server_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception:
            # Fall back to direct cmd_serve invocation in this process
            class _Args:
                host = None; port = port; no_browser = False
            return cmd_serve(_Args())
        # Poll the runtime endpoint until the server actually binds. Up
        # to 6×0.5s = 3s. Beats a fixed sleep(1.5) on slow-cold-start
        # machines (antivirus scans, first-time pyc compilation) and
        # avoids opening the browser before the server is listening.
        import time, webbrowser, urllib.request
        ready = False
        for _ in range(6):
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/runtime", timeout=0.5
                ) as _r:
                    if 200 <= _r.status < 500:
                        ready = True
                        break
            except Exception:
                time.sleep(0.5)
        if not ready:
            print(f"  {yellow('⚠')} Server didn't respond after 3s — opening anyway.")
        try:
            webbrowser.open_new_tab(f"http://127.0.0.1:{port}/setup")
        except Exception:
            pass
        print(f"\n  {green('✓')} Web wizard opened. Press Ctrl+C here when you've finished.")
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            # Best-effort terminate the spawned server when the user
            # closes the wizard, so we don't leave an orphan process.
            try:
                server_proc.terminate()
                server_proc.wait(timeout=2)
            except Exception:
                pass
            print()
            return 0

    # ── Step 1: Filesystem ────────────────────────────────────────
    print(f"  {bold('Step 1 of 3 — Filesystem')}")
    cfg_path = _config_dir() / "config.toml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    if not cfg_path.exists():
        cfg_path.write_text(
            "# Huntova config\n"
            'preferred_provider = "anthropic"\n'
        )
        if os.name != "nt":
            try: os.chmod(cfg_path, 0o600)
            except OSError: pass
    print(f"    {green('✓')} config dir: {dim(str(cfg_path.parent))}")
    try:
        import db_driver as _dbd
        db_path = _dbd._local_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        print(f"    {green('✓')} data dir:   {dim(str(db_path.parent))}")
    except Exception as e:
        print(f"    {red('✗')} data dir:   {type(e).__name__}: {e}")
    try:
        from secrets_store import _backend_label
        print(f"    {green('✓')} secrets:    {dim(_backend_label())}")
    except Exception as e:
        print(f"    {red('✗')} secrets:    {type(e).__name__}")
    print()

    # ── Step 2: Provider + key ────────────────────────────────────
    print(f"  {bold('Step 2 of 3 — Provider')}")
    print()
    # Show the 3 options
    for i, (slug, label, url, env_var) in enumerate(_PROVIDERS, start=1):
        existing = bool(os.environ.get(env_var))
        marker = green("✓ saved") if existing else dim("(not set)")
        print(f"    {bold(str(i))}. {label}")
        print(f"       {dim(url)}  {marker}")
        print()

    # Interactive picker — only if we're a TTY. Non-TTY (CI / pipe) skips.
    if not sys.stdin.isatty() or args.no_prompt:
        print(f"  {yellow('▸')} non-interactive mode — skipping key prompt.")
        print(f"    Set one of these in your env, then run `huntova hunt`:")
        for slug, _label, _url, env_var in _PROVIDERS:
            print(f"      export {env_var}=…")
        print()
        return 0

    # Already have a key? offer a fast-path
    has_any = any(os.environ.get(v) for _, _, _, v in _PROVIDERS)
    if has_any and not args.force:
        print(f"  {green('●')} A key is already saved. Skip to Step 3? [Y/n]: ", end="", flush=True)
        try:
            ans = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if ans in ("", "y", "yes"):
            chosen_slug = next((s for s, _, _, v in _PROVIDERS if os.environ.get(v)), "gemini")
        else:
            chosen_slug = None
    else:
        chosen_slug = None

    if not chosen_slug:
        print(f"  Pick provider [1-3, default 1]: ", end="", flush=True)
        try:
            raw = input().strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        idx = 1
        try:
            idx = max(1, min(3, int(raw))) if raw else 1
        except ValueError:
            idx = 1
        chosen_slug, chosen_label, chosen_url, chosen_env = _PROVIDERS[idx - 1]
        print()
        print(f"  Picked: {bold(chosen_label)}")
        print(f"  Get a key: {cyan(chosen_url)}")
        print()
        print(f"  Paste your {chosen_slug} API key (input hidden) [or blank to skip]: ", end="", flush=True)
        try:
            import getpass
            api_key = getpass.getpass("")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not api_key:
            print(f"  {yellow('▸')} skipped — set {bold(chosen_env)} in your env later, then `huntova hunt`.")
            return 0
        # Save + probe
        ok_save, _ = _spinner_step(
            f"saving key to {dim(_backend_label_safe())}",
            lambda: _save_key_safe(chosen_env, api_key),
        )
        if not ok_save:
            return 1
        os.environ[chosen_env] = api_key
        # Live AI probe
        ok_probe, probe_msg = _spinner_step(
            f"testing {chosen_slug} key with a 5-token ping",
            lambda: _probe_provider_safe(chosen_slug, chosen_env, api_key),
        )
        if not ok_probe:
            print(f"    {yellow('!')} {probe_msg}")
            print(f"    {dim('Setup will continue but the first hunt may fail.')}")
        print()

    # ── Step 3: Launch the dashboard ──────────────────────────────
    print(f"  {bold('Step 3 of 3 — Launch')}")
    print()
    print(f"    {dim('Try the 60-second pipeline:')}")
    print(f"      {cyan('$')} huntova examples install tech-recruiting")
    print(f"      {cyan('$')} huntova recipe run tech-recruiting")
    print(f"      {cyan('$')} huntova outreach send --top 5 --dry-run")
    print()
    if not args.no_launch:
        print(f"    {green('●')} Opening dashboard at http://127.0.0.1:{DEFAULT_PORT}/ …")
        print()
        # Hand off to cmd_serve (this will open the browser automatically)
        class _Args:
            host = None; port = None; no_browser = False
        return cmd_serve(_Args())
    print(f"    {bold('Setup complete.')} {dim('Try one of these next:')}")
    print()
    _tips_v1 = [
        ("huntova serve",              "open the dashboard in your browser"),
        ("huntova hunt --max-leads 5", "find your first 5 leads in the terminal"),
        ("huntova chat",               "talk to the agent in natural language"),
        ("huntova examples ls",        "10 starter recipes for common ICPs"),
        ("huntova migrate from-csv",   "import existing leads (Apollo/Clay/etc)"),
        ("huntova plugins ls",         "5 bundled plugins, more at /plugins"),
        ("huntova security audit",     "check your local install is locked down"),
        ("huntova doctor",             "full diagnostic if something looks off"),
    ]
    _w_v1 = max(len(c) for c, _ in _tips_v1)
    for _cmd, _desc in _tips_v1:
        print(f"      {cyan('$')} {_cmd.ljust(_w_v1)}   {dim('# ' + _desc)}")
    print()
    metrics_emit("cli_onboard", {"completed": True})
    return 0


def _backend_label_safe() -> str:
    try:
        from secrets_store import _backend_label
        return _backend_label()
    except Exception:
        return "secrets store"


def _save_key_safe(env_var: str, key: str) -> bool:
    from secrets_store import set_secret
    set_secret(env_var, key)
    return True


def _probe_provider_safe(slug: str, env_var: str, key: str) -> str:
    from providers import get_provider
    p = get_provider({"preferred_provider": slug, env_var: key})
    resp = p.chat(
        messages=[{"role": "user", "content": "respond with OK"}],
        max_tokens=5, temperature=0.0, timeout_s=15.0,
    )
    return (resp or "").strip()[:30] or "(empty response)"


def cmd_init(args: argparse.Namespace) -> int:
    """Initialise Huntova on this machine.

    Default mode: zero-interactive. Creates the config dir + an empty
    config.toml, prints filesystem state, exits 0. Per Kimi K2.6 round-73
    audit: any prompt during init makes HN visitors Ctrl-C — so the
    canonical flow is `huntova init` (no questions), then set
    `HV_GEMINI_KEY` (or `HV_ANTHROPIC_KEY` / `HV_OPENAI_KEY`) in the env,
    then `huntova hunt`.

    `--wizard` opts back into the friendly interactive setup for users
    who'd rather paste their key into a prompt than `export`.
    """
    cfg_path = _config_dir() / "config.toml"

    # Ensure config + (best-effort) data dirs exist
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import db_driver as _dbd
        db_path = _dbd._local_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        db_path = None

    if cfg_path.exists() and not args.force:
        print("[huntova] already initialised")
        print(f"           · config:    {cfg_path}")
        if db_path is not None:
            exists = "exists" if db_path.exists() else "will be created on first hunt"
            print(f"           · db:        {db_path} ({exists})")
        print("           Re-run with --force to overwrite, or `huntova init --wizard` to update interactively.")
        return 0

    if not args.wizard:
        # Zero-interactive happy path. Write a minimal config and bail.
        lines = [
            "# Huntova config — generated by `huntova init`",
            "# Secrets live in your OS keychain (or ~/.config/huntova/secrets.enc",
            "# if keyring isn't installed). Set HV_ANTHROPIC_KEY / HV_GEMINI_KEY /",
            "# HV_OPENAI_KEY in your env to start hunting, or run `huntova onboard`",
            "# for a friendly setup.\n",
            'preferred_provider = "anthropic"',
        ]
        cfg_path.write_text("\n".join(lines) + "\n")
        if os.name != "nt":
            try:
                os.chmod(cfg_path, 0o600)
            except OSError:
                pass
        print("[huntova] initialised")
        print(f"           · config:    {cfg_path}")
        if db_path is not None:
            print(f"           · db:        {db_path} (will be created on first hunt)")
        print("")
        print("  next step — set ONE of these in your env:")
        print("    export HV_ANTHROPIC_KEY=...   # https://console.anthropic.com/settings/keys  (default)")
        print("    export HV_OPENAI_KEY=...      # https://platform.openai.com/api-keys")
        print("    export HV_GEMINI_KEY=...      # https://aistudio.google.com/apikey")
        print("")
        print("  then run:")
        print("    huntova hunt --max-leads 5")
        print("")
        if not _telemetry_consent():
            print("  (Optional: `huntova telemetry enable` to share anonymous usage stats.)")
        metrics_emit("cli_init", {"provider": "anthropic", "had_key": False, "wizard": False})
        return 0

    # --wizard: original friendly interactive flow
    print("\n  Welcome to Huntova — local-first lead-gen super-tool.")
    print("  Let's set up your API key. You'll need one of:\n")
    for slug, label, url, _ in _PROVIDERS:
        print(f"    · {label}\n      Get a key: {url}\n")

    chosen_label = _prompt_choice(
        "Which provider would you like to start with?",
        [p[1] for p in _PROVIDERS],
        default_idx=0,
    )
    chosen_idx = [p[1] for p in _PROVIDERS].index(chosen_label)
    slug, _, url, env_var = _PROVIDERS[chosen_idx]
    print(f"\n  Get your {slug} key from: {url}")

    api_key = _prompt(f"Paste your {slug} API key (or leave blank to skip)", "")
    saved_to = "(none — set later via env var or `huntova init --wizard --force`)"
    if api_key:
        try:
            from secrets_store import set_secret, _backend_label
            set_secret(env_var, api_key)
            saved_to = _backend_label()
        except Exception as e:
            print(f"[huntova] couldn't save to secrets store: {e}")
            print("           falling back to config.toml (less secure)")

    lines = [
        "# Huntova config — generated by `huntova init --wizard`",
        "# This file is safe to commit. Secrets live in your OS keychain",
        "# (or ~/.config/huntova/secrets.enc if keyring isn't installed).\n",
        f'preferred_provider = "{slug}"',
    ]
    if api_key and saved_to.startswith("(none"):
        lines.append(f'{env_var} = "{api_key}"')
    cfg_path.write_text("\n".join(lines) + "\n")
    if os.name != "nt":
        try:
            os.chmod(cfg_path, 0o600)
        except OSError:
            pass

    print(f"\n  Saved.")
    print(f"     · config: {cfg_path}")
    print(f"     · key store: {saved_to}")
    print(f"\n  Run `huntova serve` to start the local app on http://127.0.0.1:{DEFAULT_PORT}\n")
    if not _telemetry_consent():
        print("  (Optional: `huntova telemetry enable` to share anonymous usage stats.)")
    metrics_emit("cli_init", {"provider": slug, "had_key": bool(api_key), "wizard": True})
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Diagnostic dump for support tickets. Returns non-zero exit code
    on any critical failure (missing AI key, unreachable SearXNG,
    unwritable data dir, AI probe failure) so CI scripts and scripted
    smoke tests can gate on it."""
    # Hydrate env so doctor sees the same keys that `huntova serve` would.
    os.environ.setdefault("APP_MODE", "local")
    _hydrate_env_from_local_config()

    fail = False  # any critical check failure flips to True for non-zero exit

    print(f"huntova {VERSION}")
    print(f"python  {sys.version.split()[0]}")
    print(f"config  {_config_dir()}")
    print(f"APP_MODE={os.environ.get('APP_MODE', '(unset)')}")
    print("")
    # API keys: check provider abstraction's view (which probes
    # settings → secrets_store → env)
    try:
        from providers import list_available_providers
        avail = list_available_providers()
        if avail:
            print(f"providers configured: {', '.join(avail)}")
        else:
            print("providers: ✗ (none — run `huntova onboard` to add a key)")
            fail = True
    except Exception as e:
        print(f"providers: ✗ probe failed — {e}")
        fail = True
    try:
        from secrets_store import _backend_label, list_secret_names
        print(f"secrets backend: {_backend_label()}")
        names = list_secret_names()
        print(f"  stored names: {', '.join(names) if names else '(none)'}")
    except Exception as e:
        print(f"secrets backend: (probe failed: {e})")
    print("")
    # SearXNG reachability — flag JSON-API failures clearly so users
    # understand why hunts return nothing on public instances. Under
    # --quick (CI smoke runs / scripted gates) we still HEAD-ping the
    # endpoint — Kimi K2.6 round-73 fix: a bad SEARXNG_URL is the #1
    # support ticket if doctor doesn't catch it. We just skip the full
    # JSON-API round-trip so a fresh CI runner without SearXNG can pass.
    searxng = os.environ.get("SEARXNG_URL", "").strip() or "http://127.0.0.1:8888"
    if args.quick:
        # HEAD ping with 3s timeout — fast enough for CI, real enough
        # to catch obvious typos in HV_SEARXNG_URL.
        try:
            import urllib.request
            req = urllib.request.Request(searxng.rstrip('/'), method="HEAD")
            with urllib.request.urlopen(req, timeout=3) as r:
                code = getattr(r, "status", None) or r.getcode()
            print(f"searxng: ✓ HEAD reachable at {searxng} (HTTP {code}) — JSON-API probe skipped under --quick")
        except Exception as e:
            print(f"searxng: ⚠ HEAD probe failed at {searxng} ({type(e).__name__}) — full JSON probe skipped under --quick")
            print("         → not flipping exit code (CI runners often have no SearXNG); fix HV_SEARXNG_URL for production")
    else:
        try:
            import urllib.request
            import json as _json
            req = urllib.request.Request(
                f"{searxng.rstrip('/')}/search?q=huntova_smoke&format=json",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=6) as r:
                body = r.read(2048)
                try:
                    _json.loads(body)
                    print(f"searxng: ✓ JSON API reachable at {searxng}")
                except Exception:
                    print(f"searxng: ⚠ {searxng} responded but JSON API is disabled")
                    print("         → self-host SearXNG (see README) and enable `json` in search.formats")
                    fail = True
        except Exception as e:
            print(f"searxng: ✗ unreachable at {searxng} ({type(e).__name__})")
            print("         → run SearXNG locally: `docker run -d --name=searxng -p 8888:8080 searxng/searxng`")
            fail = True
    print("")
    # Local data dir writability — first hunt fails opaquely if the
    # SQLite file can't be created (rare but happens on locked-down
    # corporate machines or when XDG_DATA_HOME points to a read-only
    # mount). Probe with a touch-and-delete.
    try:
        import db_driver as _dbd
        db_path = _dbd._local_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        probe = db_path.parent / ".huntova_probe"
        probe.write_text("ok")
        probe.unlink(missing_ok=True)
        # Also report the actual db location so support tickets are useful
        exists = "exists" if db_path.exists() else "will be created on first hunt"
        print(f"data dir: ✓ writable at {db_path.parent} ({exists})")
    except Exception as e:
        print(f"data dir: ✗ NOT writable — {type(e).__name__}: {e}")
        print("         → fix permissions on ~/.local/share/huntova or set HUNTOVA_DB_PATH")
        fail = True
    # Playwright availability — agent runs with deep-qualify when
    # Playwright is installed; falls back to requests-only mode silently
    # otherwise. Surface this so users understand why hunt quality
    # varies. NOT a hard failure — agent still works without it.
    try:
        from importlib.util import find_spec
        if find_spec("playwright"):
            try:
                # Check Chromium browser is actually installed (not just the lib)
                import subprocess
                out = subprocess.run(
                    [sys.executable, "-c",
                     "from playwright.sync_api import sync_playwright; "
                     "p = sync_playwright().start(); "
                     "print(p.chromium.executable_path); p.stop()"],
                    capture_output=True, text=True, timeout=10,
                )
                if out.returncode == 0 and out.stdout.strip():
                    print("playwright: ✓ chromium browser installed")
                else:
                    print("playwright: ⚠ library installed but Chromium not — run `playwright install chromium`")
                    print("            → agent will degrade to requests-only mode (lower lead quality)")
            except Exception as e:
                print(f"playwright: ⚠ install probe failed ({type(e).__name__}) — agent will degrade gracefully")
        else:
            print("playwright: ⚠ not installed — `pip install playwright && playwright install chromium`")
            print("            → agent runs in requests-only mode (lower lead quality, no deep-qualify)")
    except Exception as e:
        print(f"playwright: (probe error: {e})")
    # Plugin loading probe (Kimi round-76): bundled plugins (csv-sink,
    # dedup-by-domain, slack-ping, recipe-adapter, adaptation-rules) all
    # need to import without ImportError. A single missing transitive dep
    # in a weird pipx environment can break one plugin silently — this
    # surfaces the failure at doctor time instead of mid-hunt.
    try:
        from plugins import get_registry, reset_for_tests
        try:
            reset_for_tests()
        except Exception:
            pass
        reg = get_registry()
        try:
            reg.discover()
        except Exception as e:
            print(f"plugins: ⚠ discover() raised {type(e).__name__}: {str(e)[:80]}")
        listed = []
        try:
            listed = reg.list_plugins()
        except Exception:
            pass
        names = []
        for p in listed:
            n = p.get("name") if isinstance(p, dict) else getattr(p, "name", None)
            if n:
                names.append(str(n))
        plug_errors = []
        try:
            plug_errors = reg.errors() or []
        except Exception:
            pass
        if plug_errors:
            print(f"plugins: ⚠ {len(plug_errors)} failed to load:")
            for slug, err in plug_errors[:5]:
                print(f"           · {slug}: {err[:80]}")
            print("         → likely a missing pip dep — check pyproject extras")
        if names:
            print(f"plugins: ✓ {len(names)} loaded ({', '.join(names[:6])}{'...' if len(names) > 6 else ''})")
        elif not plug_errors:
            print("plugins: ⚠ none discovered — bundled plugins should be present")
    except Exception as e:
        print(f"plugins: (probe error: {type(e).__name__}: {str(e)[:80]})")
    print("")
    raw_env = {
        k: bool(os.environ.get(k))
        for k in ("HV_GEMINI_KEY", "HV_ANTHROPIC_KEY", "HV_OPENAI_KEY",
                  "DATABASE_URL", "SEARXNG_URL", "STRIPE_SECRET_KEY")
    }
    for k, v in raw_env.items():
        print(f"  env {k:24} {'set' if v else '(unset)'}")
    # Optional live AI probe — sends a minimal "ping" request to the
    # configured provider. Confirms the key is genuinely valid (vs
    # just present in the env). Skipped with --quick to avoid network
    # calls during CI / scripted runs.
    if not args.quick:
        print("")
        try:
            from providers import get_provider
            try:
                p = get_provider()
            except RuntimeError as e:
                print(f"AI probe: skipped — {e}")
                # Don't override fail flag — earlier checks (SearXNG /
                # data-dir / providers) may have already failed. CI gates
                # like `huntova doctor && huntova hunt` rely on this exit
                # code to short-circuit on a broken install.
                return 1 if fail else 0
            print(f"AI probe: querying {p.name} with a 5-token ping…")
            try:
                resp = p.chat(
                    messages=[{"role": "user", "content": "respond with OK"}],
                    max_tokens=5,
                    temperature=0.0,
                    timeout_s=15.0,
                )
                snippet = (resp or "").strip()[:40] or "(empty response)"
                print(f"AI probe: ✓ {p.name} responded: {snippet!r}")
            except Exception as e:
                print(f"AI probe: ✗ {p.name} failed — {type(e).__name__}: {str(e)[:120]}")
                print("          → key may be invalid, expired, or rate-limited")
                return 1
        except Exception as e:
            print(f"AI probe: ✗ unexpected error: {type(e).__name__}: {e}")
            fail = True

    # Optional: deliverability pre-flight (SPF/DKIM/DMARC + MX). Only
    # runs when --email is passed so the default `huntova doctor` stays
    # fast.
    if getattr(args, "email", False):
        try:
            uid = _bootstrap_local_env()
            if uid is not None:
                from cli_deliverability import doctor_email_check
                rc = doctor_email_check(uid, args)
                if rc != 0:
                    fail = True
        except Exception as e:
            print(f"deliverability probe: ✗ unexpected error: {type(e).__name__}: {e}")

    return 1 if fail else 0


def cmd_security(args: argparse.Namespace) -> int:
    """Local security audit — file modes, plaintext fallbacks, env leaks.

    Lead-gen-relevant subset of common security pre-flight checks.
    Prints a coloured report; exit code 1 if any sev-1 fires, 2 if only
    sev-2, else 0. With --json emits a machine-readable list instead.
    """
    try:
        from tui import bold, dim, green, red, yellow, cyan
    except ImportError:
        bold = dim = green = red = yellow = cyan = lambda s: s

    import json as _json
    import stat as _stat

    findings: list[dict] = []

    def add(check: str, sev: int, status: str, detail: str, hint: str = "") -> None:
        findings.append({"check": check, "sev": sev, "status": status,
                         "detail": detail, "hint": hint})

    posix = (os.name != "nt")

    def _mode_check(p: Path, label: str, sev_if_loose: int) -> None:
        if not p.exists():
            add(label, 3, "skip", f"{p} not present", "")
            return
        if not posix:
            add(label, 3, "info", f"{p} (mode check skipped on Windows)", "")
            return
        try:
            mode = _stat.S_IMODE(p.stat().st_mode)
        except OSError as e:
            add(label, 2, "error", f"stat({p}) failed: {e}", "fix permissions on the file")
            return
        if mode == 0o600:
            add(label, 3, "ok", f"{p} mode 0o{mode:03o}", "")
        else:
            add(label, sev_if_loose, "warn",
                f"{p} mode 0o{mode:03o} (expected 0o600)",
                f"chmod 600 {p}")

    cfg_dir = _config_dir()

    # 1. config.toml file mode
    _mode_check(cfg_dir / "config.toml", "config.toml mode", sev_if_loose=2)

    # 2. secrets.enc fallback file mode
    _mode_check(cfg_dir / "secrets.enc", "secrets.enc mode", sev_if_loose=1)

    # 3. plaintext secrets file present at all. The diagnostic depends
    # on which backend is active — if keyring or encrypted-file is
    # already the active store, secrets.json is a stale legacy file
    # the user should `rm`. If neither is available, plaintext is the
    # active fallback, which is a real security warning.
    plain = cfg_dir / "secrets.json"
    if plain.exists():
        try:
            from secrets_store import _backend_label
            backend = _backend_label()
        except Exception:
            backend = "unknown"
        if backend in ("keyring", "encrypted-file"):
            add("plaintext secrets.json", 1, "warn",
                f"{plain} exists alongside active {backend} backend — legacy file, safe to remove",
                "rm secrets.json")
        else:
            add("plaintext secrets.json", 1, "warn",
                f"{plain} exists — plaintext fallback engaged ({backend})",
                "install `keyring` or `cryptography`, run `huntova onboard` to migrate, then rm secrets.json")
    else:
        add("plaintext secrets.json", 3, "ok", "no plaintext fallback file", "")

    # 4. DB file mode
    try:
        from db_driver import _local_db_path
        _mode_check(_local_db_path(), "db.sqlite mode", sev_if_loose=2)
    except Exception as e:
        add("db.sqlite mode", 2, "error", f"could not resolve db path: {e}", "")

    # 5. Daemon plist / unit file modes
    try:
        from huntova_daemon import _macos_plist_path, _linux_unit_path
        for fn, label in ((_macos_plist_path, "launchd plist mode"),
                          (_linux_unit_path,  "systemd unit mode")):
            try:
                _mode_check(fn(), label, sev_if_loose=2)
            except Exception as e:
                add(label, 2, "error", f"path resolve failed: {e}", "")
    except Exception as e:
        add("daemon files", 3, "info", f"daemon module unavailable: {e}", "")

    # 6. HV_VERBOSE_LOGS in env
    if os.environ.get("HV_VERBOSE_LOGS"):
        add("HV_VERBOSE_LOGS", 3, "info",
            "set — logs may include sensitive paths/queries",
            "unset HV_VERBOSE_LOGS in production shells")
    else:
        add("HV_VERBOSE_LOGS", 3, "ok", "unset", "")

    # 7. HV_SECRET_KEY status
    sk = os.environ.get("HV_SECRET_KEY", "").strip()
    if not sk:
        add("HV_SECRET_KEY", 2, "warn",
            "unset — config.py will use the local dev fallback",
            "export HV_SECRET_KEY=$(python -c 'import secrets;print(secrets.token_urlsafe(48))')")
    elif sk == "huntova-dev-secret-LOCAL-ONLY":
        add("HV_SECRET_KEY", 1, "warn", "set to dev fallback literal",
            "regenerate with `python -c 'import secrets;print(secrets.token_urlsafe(48))'`")
    else:
        add("HV_SECRET_KEY", 3, "ok", f"set ({len(sk)} chars)", "")

    # 8. HTTP(S)_PROXY env vars
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        v = os.environ.get(var)
        if v:
            add(var, 2, "warn",
                f"set to {v} — could MITM outbound AI / SearXNG traffic",
                "double-check this is a trusted proxy; unset for production")

    # 9. Stripe / webhook secrets pasted into config.toml
    cfg_toml = cfg_dir / "config.toml"
    if cfg_toml.exists():
        try:
            txt = cfg_toml.read_text(errors="ignore")
            leaked: list[str] = []
            for line in txt.splitlines():
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                up = s.upper()
                if up.startswith("STRIPE_") or up.startswith("WEBHOOK_") or \
                   "STRIPE_SECRET" in up or "WEBHOOK_SECRET" in up:
                    leaked.append(s.split("=", 1)[0].strip())
            if leaked:
                add("config.toml secret leak", 1, "warn",
                    f"Stripe/webhook keys in plaintext: {', '.join(leaked)}",
                    "remove from config.toml; store via `huntova onboard` (writes to keychain)")
            else:
                add("config.toml secret leak", 3, "ok",
                    "no STRIPE_*/WEBHOOK_* keys in config.toml", "")
        except Exception as e:
            add("config.toml secret leak", 2, "error", f"read failed: {e}", "")
    else:
        add("config.toml secret leak", 3, "skip", "config.toml absent", "")

    # 10. Keychain backend in use
    try:
        from secrets_store import _backend_label
        backend = _backend_label()
        if backend == "plaintext-file":
            add("secrets backend", 1, "warn",
                "plaintext-file fallback in use",
                "pip install keyring (preferred) or cryptography, then `huntova onboard`")
        elif backend == "encrypted-file":
            add("secrets backend", 3, "info",
                "encrypted-file (Fernet) — OK but keyring is stronger",
                "pip install keyring for OS-native protection")
        else:
            add("secrets backend", 3, "ok", backend, "")
    except Exception as e:
        add("secrets backend", 2, "error", f"probe failed: {e}", "")

    n1 = sum(1 for f in findings if f["sev"] == 1 and f["status"] != "ok")
    n2 = sum(1 for f in findings if f["sev"] == 2 and f["status"] != "ok")
    exit_code = 1 if n1 else (2 if n2 else 0)

    if args.json:
        print(_json.dumps({"findings": findings, "exit_code": exit_code}, indent=2))
    else:
        print(bold("Huntova security audit"))
        print(dim(f"  config dir: {cfg_dir}"))
        print("")
        sev_colour = {1: red, 2: yellow, 3: green}
        sev_tag = {1: "SEV-1", 2: "SEV-2", 3: "info "}
        for f in findings:
            colour = sev_colour.get(f["sev"], cyan)
            tag = colour(sev_tag.get(f["sev"], "  ?  "))
            print(f"  [{tag}] {bold(f['check'].ljust(28))}  {f['detail']}")
            if f["hint"]:
                print(f"           {dim('→ ' + f['hint'])}")
        print("")
        if n1:
            print(red(bold(f"  {n1} sev-1 finding(s) — fix before launch")))
        if n2:
            print(yellow(f"  {n2} sev-2 finding(s) — review"))
        if not n1 and not n2:
            print(green("  All checks passed."))

    return exit_code


def cmd_version(args: argparse.Namespace) -> int:
    print(VERSION)
    return 0


# ── Opt-in telemetry (Kimi round-72 spec) ─────────────────────────
# Three events only: try_submit (server-side), cli_init, cli_hunt.
# Opt-in via `huntova telemetry enable` — never an interactive prompt
# during init (would break scripting). Consent flag lives in
# ~/.config/huntova/.telemetry. No keys, no queries, no PII shipped.

_TELEMETRY_ENDPOINT_DEFAULT = "https://huntova.com/api/_metric"


def _telemetry_flag_path():
    return _config_dir() / ".telemetry"


def _telemetry_consent() -> bool:
    return _telemetry_flag_path().exists()


def metrics_emit(event: str, props: dict | None = None) -> None:
    """Fire-and-forget telemetry. Never raises, never logs an error
    line — telemetry must not crash or noise up the CLI."""
    if not _telemetry_consent():
        return
    try:
        import platform as _plat
        import json as _json
        import urllib.request
        endpoint = os.environ.get("HV_METRICS_URL", _TELEMETRY_ENDPOINT_DEFAULT)
        payload = {
            "event": event,
            "platform": _plat.system(),
            "version": VERSION,
            "props": props or {},
        }
        body = _json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            endpoint, data=body, method="POST",
            headers={"Content-Type": "application/json", "User-Agent": f"huntova/{VERSION}"},
        )
        urllib.request.urlopen(req, timeout=3.0).read(64)
    except Exception:
        pass


def cmd_settings(args: argparse.Namespace) -> int:
    """Local-CLI settings toggle. Today supports `auto_update` (alias
    `auto_update_on_launch`) which controls whether `huntova serve`
    runs `pipx install --force` automatically when a newer release is
    on GitHub.

    Examples:
        huntova settings auto_update on
        huntova settings auto_update off
        huntova settings auto_update show
    """
    cfg = Path.home() / ".config" / "huntova" / "config.toml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    key = (getattr(args, "key", "") or "").strip().lower()
    val = (getattr(args, "value", "") or "").strip().lower()
    if not key:
        print("[huntova] usage: huntova settings <key> <on|off|show>")
        print("           keys: auto_update")
        return 1
    canonical = "auto_update_on_launch" if key in ("auto_update", "auto_update_on_launch") else None
    if canonical is None:
        print(f"[huntova] unknown setting: {key!r} (try `auto_update`)")
        return 1
    # Read existing TOML, mutate, write back. Keep it human-editable —
    # we don't pull in a TOML writer, just append/replace one line.
    # Strip any existing line that assigns the canonical key, regardless
    # of indentation (so a key inside a `[section]` is also removed).
    # Crucially: we append the new value to the end-of-file BEFORE any
    # `[section]` would interpret it as inside that section. We achieve
    # this by inserting before the FIRST `[section]` header if any
    # exists, otherwise appending at the end.
    import re as _re
    existing = cfg.read_text() if cfg.exists() else ""
    _key_re = _re.compile(r"^\s*" + _re.escape(canonical) + r"\s*=", _re.MULTILINE)
    lines = [ln for ln in existing.splitlines() if not _key_re.match(ln)]
    if val == "show" or not val:
        cur = "off"
        for ln in existing.splitlines():
            if _key_re.match(ln):
                cur = ln.split("=", 1)[1].strip()
                break
        print(f"[huntova] {canonical} = {cur}")
        return 0
    new_val = "true" if val in ("on", "true", "yes", "1") else (
              "false" if val in ("off", "false", "no", "0") else None)
    if new_val is None:
        print(f"[huntova] expected on / off / show, got {val!r}")
        return 1

    # Find the first section header — we must insert above it so the
    # new top-level key isn't sucked into that section's body.
    insert_at = 0
    for i, ln in enumerate(lines):
        if _re.match(r"^\s*\[", ln):
            insert_at = i
            break
    else:
        insert_at = len(lines)
    lines.insert(insert_at, f'{canonical} = {new_val}')
    cfg.write_text("\n".join(lines).strip() + "\n")
    if new_val == "true":
        print(f"[huntova] {canonical} = on  (next `huntova serve` will auto-upgrade if a release is out)")
    else:
        print(f"[huntova] {canonical} = off")
    return 0
    print(f"[huntova] expected on / off / show, got {val!r}")
    return 1


def cmd_telemetry(args: argparse.Namespace) -> int:
    """Opt-in usage telemetry: enable / disable / status.

    Three events fire when enabled: cli_init (after init succeeds),
    cli_hunt (after a hunt finishes), and try_submit (server-side
    on /api/try POST). No keys, no queries, no PII — just platform,
    version, and event-level counts.
    """
    sub = (args.action or "status").lower()
    flag = _telemetry_flag_path()
    if sub == "enable":
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text("on\n")
        print(f"[huntova] telemetry: ON ({flag})")
        print("           events: cli_init, cli_hunt (no keys/queries/PII)")
        print("           disable any time with `huntova telemetry disable`")
        return 0
    if sub == "disable":
        try:
            flag.unlink()
            print(f"[huntova] telemetry: OFF ({flag} removed)")
        except FileNotFoundError:
            print("[huntova] telemetry: already off")
        return 0
    # status (default)
    if _telemetry_consent():
        print(f"[huntova] telemetry: ON ({flag})")
    else:
        print("[huntova] telemetry: OFF")
        print("           enable with `huntova telemetry enable` (opt-in only)")
    return 0


_UPDATE_GIT_URL = "git+https://github.com/enzostrano/huntova-public.git"
_UPDATE_RELEASES_API = "https://api.github.com/repos/enzostrano/huntova-public/releases/latest"
def _update_cache_path() -> Path:
    """Where the cached "latest GitHub tag" check lives. Same dir as
    secrets/db so a `pipx reinstall --force` clears it naturally."""
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    p = Path(base) / "huntova"
    p.mkdir(parents=True, exist_ok=True)
    return p / ".update-check"
_UPDATE_CACHE_HOURS = 6


def _check_latest_release() -> str | None:
    """Hit GitHub Releases API for the latest tag, cached for
    `_UPDATE_CACHE_HOURS` hours. Returns the tag string (e.g.
    `v0.1.0a134`) or None on any failure — callers must treat
    None as "couldn't check, carry on"."""
    import json as _json
    import time as _time
    cache_path = _update_cache_path()
    try:
        if cache_path.exists():
            mtime = cache_path.stat().st_mtime
            if (_time.time() - mtime) < (_UPDATE_CACHE_HOURS * 3600):
                cached = _json.loads(cache_path.read_text())
                return cached.get("tag")
    except Exception:
        pass
    # Live fetch — short timeout so we never block the CLI for long.
    import urllib.request
    try:
        req = urllib.request.Request(
            _UPDATE_RELEASES_API,
            headers={"User-Agent": "huntova-cli/" + VERSION,
                     "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=2.5) as r:
            data = _json.loads(r.read().decode("utf-8"))
            tag = (data.get("tag_name") or "").strip()
            if tag:
                try:
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    cache_path.write_text(_json.dumps({"tag": tag,
                                                       "checked_at": _time.time()}))
                except Exception:
                    pass
                return tag
    except Exception:
        pass
    return None


def _is_update_available() -> tuple[bool, str | None]:
    """Compare current `VERSION` to the latest GitHub tag. Returns
    (update_available, latest_tag). Tag-aware comparison: strips
    the leading `v` and does a semver-ish lexicographic compare on
    the alpha-suffix integer."""
    latest = _check_latest_release()
    if not latest:
        return False, None
    tag = latest.lstrip("v")
    cur = VERSION.lstrip("v")
    if tag == cur:
        return False, latest
    # Coerce both to (major, minor, patch, alpha-N) tuples so
    # 0.1.0a134 > 0.1.0a99. Falls back to string compare on parse error.
    def _parse(s: str) -> tuple:
        try:
            base, _, suffix = s.partition("a")
            parts = tuple(int(x) for x in base.split("."))
            # Alpha is *pre-release*: 0.1.0a134 < 0.1.0 (stable). Without
            # this, the absent suffix coerced to `0` made stable look
            # smaller than every alpha (no upgrade banner on cutover).
            if suffix.isdigit():
                return parts + (0, int(suffix))
            return parts + (1, 0)
        except Exception:
            return (s,)  # type: ignore[return-value]
    try:
        return (_parse(tag) > _parse(cur)), latest
    except Exception:
        # Conservative: if we can't reliably compare, claim "no update".
        # The previous fallback was `(tag != cur)` which would read
        # `("" != "0.1.0a174") == True` if the GitHub response was
        # malformed JSON, forcing every user to see a fake update banner
        # until the cache expired.
        return False, latest


def _auto_update_enabled() -> bool:
    """Read the `auto_update_on_launch` flag from local config.toml.

    Defaults to False so a fresh install never silently upgrades the
    binary the user just ran. Users opt in with:
        huntova settings auto_update on
    or by setting HV_AUTO_UPDATE=1 in their environment.
    """
    if str(os.environ.get("HV_AUTO_UPDATE", "")).strip().lower() in ("1", "true", "yes", "on"):
        return True
    try:
        # Stability fix (audit wave 27): the previous version hardcoded
        # `~/.config/huntova/config.toml`, ignoring `XDG_CONFIG_HOME`.
        # The rest of the codebase (`_config_dir`, `cmd_settings`,
        # `_hydrate_env_from_local_config`, `secrets_store`, etc.) all
        # honor XDG via `os.environ.get("XDG_CONFIG_HOME") or
        # str(Path.home() / ".config")`. Users on Linux/CI who set
        # XDG_CONFIG_HOME would write `auto_update_on_launch=on` via
        # `huntova settings` (which used the XDG path) and the
        # auto-update check would silently miss it (always False),
        # disabling the feature for them. Match the canonical
        # config-dir lookup.
        _base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        cfg = Path(_base) / "huntova" / "config.toml"
        if not cfg.exists():
            return False
        try:
            import tomllib  # py311+
        except ImportError:
            return False
        with open(cfg, "rb") as f:
            data = tomllib.load(f) or {}
        return bool(data.get("auto_update_on_launch")) or bool(
            ((data.get("settings") or {}).get("auto_update_on_launch")))
    except Exception:
        return False


def _run_self_update() -> bool:
    """Best-effort `huntova update` invocation via subprocess so the
    parent doesn't try to overwrite its own running module. Returns
    True if the update appeared to succeed."""
    import shutil
    import subprocess
    pipx = shutil.which("pipx")
    if not pipx:
        return False
    spec = "git+https://github.com/enzostrano/huntova-public.git"
    try:
        r = subprocess.run([pipx, "install", "--force", spec],
                           capture_output=True, timeout=180, text=True)
        return r.returncode == 0
    except Exception:
        return False


def _maybe_prompt_update(force: bool = False) -> None:
    """Print a one-line update banner if a newer release is on
    GitHub. Silent on success / no-update / network failure unless
    `force=True`. Pinned to ≤ 4 lines so it doesn't clobber the
    serve banner.

    When `auto_update_on_launch` is enabled (config.toml or
    HV_AUTO_UPDATE env), this also runs the upgrade in-place before
    returning, so the next `huntova serve` boot is on the new version.
    """
    try:
        from tui import bold, dim, green, yellow, cyan
    except ImportError:
        bold = dim = green = yellow = cyan = (lambda s: s)
    avail, latest = _is_update_available()
    if not avail:
        if force:
            tag = latest or VERSION
            print(f"  {green('●')} you're on {bold(tag)} {dim('(latest)')}")
        return
    print()
    print(f"  {yellow('▲')} {bold('Huntova update available:')} "
          f"{dim(VERSION)} → {bold(latest or '?')}")
    if _auto_update_enabled():
        print(f"  {dim('auto-update is on — running pipx install --force…')}")
        ok = _run_self_update()
        if ok:
            print(f"  {green('✓')} upgraded. Restart `huntova serve` to load {bold(latest or '?')}.")
        else:
            print(f"  {yellow('!')} auto-update failed; run {cyan('huntova update')} manually.")
    else:
        print(f"  Run {cyan('huntova update')} to upgrade. "
              f"{dim('(or enable auto-update: huntova settings auto_update on)')}")
    print()


def cmd_update(args: argparse.Namespace) -> int:
    """Self-upgrade. Pulls the latest from
    `enzostrano/huntova-public` via pipx (--force re-fetches even
    when the version string matches the cached spec). Falls back
    to `pip install --user --upgrade` when pipx isn't available.
    """
    import shutil
    import subprocess
    if getattr(args, "check", False):
        avail, latest = _is_update_available()
        if avail:
            print(f"[huntova] update available: {VERSION} → {latest}")
            return 0
        print(f"[huntova] you're on the latest ({latest or VERSION}).")
        return 0
    if shutil.which("pipx"):
        cmd = ["pipx", "install", "--force", _UPDATE_GIT_URL]
    elif shutil.which("pip"):
        cmd = [sys.executable, "-m", "pip", "install", "--user",
               "--upgrade", "--force-reinstall", _UPDATE_GIT_URL]
    else:
        print("[huntova] neither pipx nor pip found — install pipx and re-run",
              file=sys.stderr)
        return 1
    print(f"[huntova] running: {' '.join(cmd)}")
    try:
        rc = subprocess.call(cmd)
    except KeyboardInterrupt:
        return 130
    # Bust the cached "latest" so the next launch immediately sees
    # the new version as current.
    try:
        _update_cache_path().unlink(missing_ok=True)
    except Exception:
        pass
    return rc


def _bootstrap_local_env() -> int | None:
    """Common pre-flight for read-only CLI commands. Sets APP_MODE,
    hydrates env, ensures DB is initialised, returns the local user id.

    Returns user_id on success, None on failure (and prints a
    user-facing error to stderr).
    """
    os.environ.setdefault("APP_MODE", "local")
    _hydrate_env_from_local_config()
    try:
        import asyncio as _asyncio
        import db as _db
        _db.init_db_sync()
        from auth import _ensure_local_user
        user = _asyncio.run(_ensure_local_user())
        return user["id"]
    except Exception as e:
        print(f"[huntova] bootstrap failed: {e}", file=sys.stderr)
        return None


def cmd_ls(args: argparse.Namespace) -> int:
    """List leads stored locally — top-N by fit score, newest first."""
    user_id = _bootstrap_local_env()
    if user_id is None:
        return 1
    import asyncio as _asyncio
    import db as _db
    # Pull a generous batch when ANY filter is set so we have enough
    # candidates after narrowing.
    _has_filter = bool(args.filter or getattr(args, "status", "") or
                       getattr(args, "reply_class", "") or
                       int(getattr(args, "min_fit", 0) or 0) > 0)
    leads = _asyncio.run(_db.get_leads(user_id, limit=max(1, int(args.limit) * 4 if _has_filter else int(args.limit))))
    if not leads:
        print("[huntova] no leads yet — run `huntova hunt` first.")
        return 0
    # Apply the new shortcut flags before the substring filter.
    _status = (getattr(args, "status", "") or "").strip().lower()
    if _status:
        leads = [l for l in leads if (l.get("email_status") or "new").lower() == _status]
    _reply = (getattr(args, "reply_class", "") or "").strip().lower()
    if _reply:
        leads = [l for l in leads if (l.get("_reply_class") or "").lower() == _reply]
    _min_fit = int(getattr(args, "min_fit", 0) or 0)
    if _min_fit > 0:
        leads = [l for l in leads if int(l.get("fit_score") or 0) >= _min_fit]
    if _has_filter and not leads:
        print(f"[huntova] no leads match the filter combo "
              f"(status={_status!r} reply={_reply!r} min_fit={_min_fit}).")
        return 0
    # Optional filter — accepts "field:value" or just "value" (matches
    # any of the common text fields). Case-insensitive substring.
    if args.filter:
        f = args.filter.strip()
        if ":" in f:
            field, _, needle = f.partition(":")
            field = field.strip().lower()
            needle = needle.strip().lower()
            leads = [l for l in leads if needle in str(l.get(field, "")).lower()]
        else:
            needle = f.lower()
            scan_fields = ("org_name", "country", "city", "contact_name",
                           "contact_email", "why_fit", "production_gap",
                           "email_subject", "email_status")
            leads = [
                l for l in leads
                if any(needle in str(l.get(k, "")).lower() for k in scan_fields)
            ]
        if not leads:
            print(f"[huntova] no leads match filter {args.filter!r}.")
            return 0
        # Re-cap to limit after filtering
        leads = leads[: int(args.limit)]
    # Sort by fit_score desc for the headline ranking.
    try:
        leads.sort(key=lambda l: int(l.get("fit_score") or 0), reverse=True)
    except Exception:
        pass
    if args.format == "json":
        import json as _json
        print(_json.dumps(leads, indent=2, default=str))
        return 0
    # Compact table. ANSI-coloured fit chip if stdout is a TTY.
    color = sys.stdout.isatty()
    def _chip(score: int) -> str:
        if not color:
            return f"[{score}/10]"
        if score >= 8:
            return f"\033[1;32m[{score}/10]\033[0m"
        if score >= 6:
            return f"\033[1;33m[{score}/10]\033[0m"
        return f"\033[2m[{score}/10]\033[0m"
    print(f"\n{len(leads)} leads (sorted by fit):\n")
    for lead in leads:
        try:
            fit = int(lead.get("fit_score") or 0)
        except Exception:
            fit = 0
        org = (lead.get("org_name") or "?")[:38]
        country = (lead.get("country") or "")[:14]
        es = (lead.get("email_status") or "new")[:12]
        why = (lead.get("why_fit") or "")[:90]
        print(f"  {_chip(fit)} {org:<38}  {country:<14}  {es:<12}  {why}")
    print("")
    return 0


def cmd_quickstart(args: argparse.Namespace) -> int:
    """Single-command interactive walkthrough for first-time users.

    Guides through: pick a playbook → install → run a 5-lead hunt
    → preview AI-drafted emails → suggest next steps. Designed for
    the demo / first-impression path. Detects already-onboarded
    state and skips the wizard recommendation when it's not needed.
    """
    user_id = _bootstrap_local_env()
    if user_id is None:
        return 1
    try:
        from tui import bold, cyan, dim, green, red, yellow, intro, outro, note
        from tui import select as _select, SelectOption
    except ImportError:
        return 1

    intro("Huntova quickstart")
    note(
        "This walks you through the 30-second demo: pick a playbook, "
        "run a 5-lead hunt, preview drafts. Ctrl-C anytime to bail."
    )

    # ── 1. Provider configured? ────────────────────────────────
    try:
        from providers import list_available_providers
        configured = list_available_providers() or []
    except Exception:
        configured = []
    if not configured:
        print(f"\n  {red('●')} No AI provider configured.")
        print(f"  {bold('Run')} {cyan('huntova onboard')} {bold('first, then re-run quickstart.')}\n")
        return 1
    print(f"\n  {green('✓')} provider configured: {', '.join(configured[:3])}")

    # ── 2. Playbook picker ─────────────────────────────────────
    options = [
        SelectOption(value=name, label=name, hint=spec["description"][:80])
        for name, spec in _BUNDLED_EXAMPLES.items()
    ]
    print(f"\n  {bold('Pick a playbook')} (matches your offer / ICP):\n")
    chosen = _select("Which playbook?", options, default="solo-coach")
    if not chosen:
        print(f"\n  {dim('cancelled.')}\n")
        return 0

    # ── 3. Install playbook ────────────────────────────────────
    try:
        cmd_examples(argparse.Namespace(
            subcommand="install",
            name=chosen,
            force=False,
        ))
    except SystemExit:
        pass
    except Exception as e:
        print(f"  {red('✗ install failed:')} {e}\n", file=sys.stderr)
        return 1

    # ── 4. Run the hunt ────────────────────────────────────────
    print(f"\n  {bold('Running your first hunt')} {dim('(~3 minutes for 5 leads)…')}\n")
    hunt_args = argparse.Namespace(
        countries="",       # use playbook countries
        max_leads=5,
        timeout_minutes=10,
        verbose=False,
        json=False,
        dry_run=False,
        from_share="",
        explain_scores=False,
    )
    try:
        cmd_hunt(hunt_args)
    except KeyboardInterrupt:
        print(f"\n  {yellow('●')} hunt interrupted — back to quickstart.\n")
    except Exception as e:
        print(f"  {red('!')} hunt error: {e}", file=sys.stderr)

    # ── 5. Show what landed ────────────────────────────────────
    import asyncio as _aio
    import db as _db
    leads = _aio.run(_db.get_leads(user_id, limit=10)) or []
    leads = sorted(leads, key=lambda x: int(x.get("fit_score") or 0), reverse=True)[:5]
    if not leads:
        outro("Hunt finished but found no qualifying leads. "
              "Tweak the playbook (`huntova playbook ls`) or run "
              "`huntova hunt --max-leads 10` for a wider pass.")
        return 0
    print(f"\n  {bold('Top 5 leads:')}\n")
    for ld in leads:
        fit = ld.get("fit_score", "?")
        org = (ld.get("org_name") or "?")[:40]
        print(f"    [{green(str(fit))}/10]  {bold(org)}  {dim(ld.get('country',''))}")

    # ── 6. Recommend next moves ────────────────────────────────
    print(f"\n  {bold('Next steps:')}\n")
    print(f"    {cyan('huntova outreach send --top 5 --research-above 8 --dry-run')}")
    print(f"      {dim('preview hyper-personalised openers (deep-research the top tier)')}")
    print(f"    {cyan('huntova outreach send --top 5 --research-above 8 --max 5')}")
    print(f"      {dim('actually deliver via your SMTP')}")
    print(f"    {cyan('huntova schedule print --target launchd > ~/Library/LaunchAgents/com.huntova.daily.plist')}")
    print(f"      {dim('autonomous-daily mode: cadence + IMAP + pulse on schedule')}")
    print(f"    {cyan('huntova chat')}")
    print(f"      {dim('natural-language REPL — say what you want, AI dispatches the right command')}")
    print()
    return 0


def cmd_research(args: argparse.Namespace) -> int:
    """Deep-research one lead: re-crawl their website, build a persona
    dossier, and rewrite the lead's email_subject + email_body so the
    opener references something specific about them — their last
    podcast, blog post, hire, product launch, whatever the crawl
    surfaces. Saves the new draft on the lead row.

    Differentiator vs the standard hunt loop (which crawls 3-4 pages
    per qualified lead): this command crawls up to 14 pages and
    passes the combined text into a fresh AI call with an explicit
    "find the most specific personal hook in here" instruction.

    Examples:
        huntova research L17
        huntova research L17 --pages 20
        huntova research L17 --tone consultative
    """
    user_id = _bootstrap_local_env()
    if user_id is None:
        return 1
    import asyncio as _aio
    import db as _db
    try:
        from tui import bold, dim, green, red, yellow
    except ImportError:
        bold = dim = green = red = yellow = (lambda s: s)

    # ── --batch mode ─────────────────────────────────────────────
    # Iterate the top N un-researched leads matching --above and
    # recursively call cmd_research on each.
    _batch_n = int(getattr(args, "batch", 0) or 0)
    if _batch_n > 0:
        _above = float(getattr(args, "above", 0.0) or 0.0)
        leads = _aio.run(_db.get_leads(user_id, limit=2000)) or []
        # Skip already-researched, no-website, and dead-status leads.
        _DEAD = {"replied", "lost", "won", "meeting_booked", "ignored"}
        candidates = [
            l for l in leads
            if not l.get("_researched_at")
            and float(l.get("fit_score") or 0) >= _above
            and (l.get("org_website") or "").strip()
            and (l.get("email_status") or "new").lower() not in _DEAD
            and l.get("lead_id")
        ]
        candidates.sort(key=lambda x: float(x.get("fit_score") or 0), reverse=True)
        candidates = candidates[:_batch_n]
        if not candidates:
            print(f"  {dim('no eligible leads to research — every top lead is already researched, '
                          'sent, replied, or lacks an org_website.')}")
            return 0
        print(f"\n  {bold('huntova research --batch')} — {len(candidates)} lead(s)\n")
        from argparse import Namespace as _NS
        ok, fail = 0, 0
        for ld in candidates:
            sub_args = _NS(lead_id=ld.get("lead_id"),
                           pages=getattr(args, "pages", "14"),
                           tone=getattr(args, "tone", ""),
                           batch=0, above=0.0)
            try:
                rc = cmd_research(sub_args)
                if rc == 0:
                    ok += 1
                else:
                    fail += 1
            except KeyboardInterrupt:
                print(f"\n  {yellow('●')} batch interrupted at "
                      f"{ld.get('lead_id')} ({ok} done, {fail} failed)")
                return 1
            except Exception as _be:
                fail += 1
                print(f"  {red('!')} {ld.get('lead_id')}: {type(_be).__name__}: {str(_be)[:80]}")
        print(f"\n  {bold('batch summary:')} {green(str(ok))} researched, "
              f"{red(str(fail)) if fail else dim('0')} failed.\n")
        return 0 if fail == 0 else 1
    # ── /batch mode ──────────────────────────────────────────────

    lid = (args.lead_id or "").strip()
    if not lid:
        print("[huntova] usage: huntova research <lead-id>  (or --batch N --above SCORE)",
              file=sys.stderr)
        return 1
    lead = _aio.run(_db.get_lead(user_id, lid))
    if not lead:
        print(f"[huntova] no lead with id {lid!r}.", file=sys.stderr)
        return 1
    site = (lead.get("org_website") or "").strip()
    if not site:
        print(f"[huntova] {lid} has no org_website — can't crawl. "
              "Edit the lead in the dashboard first.",
              file=sys.stderr)
        return 1

    # Hydrate env so the AI provider abstraction picks up the
    # configured key (mirrors cmd_outreach).
    os.environ.setdefault("APP_MODE", "local")
    _hydrate_env_from_local_config()

    org = lead.get("org_name") or "?"
    print(f"\n  {bold('researching')} {lid} {org}\n  {dim('site:')} {site}\n")
    pages = max(4, min(int(args.pages or 14), 25))
    try:
        from app import crawl_prospect
        text, _html, n_crawled = crawl_prospect(site, max_subpages=pages)
    except Exception as e:
        print(f"  {red('✗ crawl failed:')} {type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
        return 1
    text = (text or "").strip()
    print(f"  {green('✓')} crawled {n_crawled} pages, {len(text):,} chars of text")
    if len(text) < 400:
        print(f"  {yellow('!')} not enough content to research — skipping.", file=sys.stderr)
        return 1

    # Compose a fresh AI prompt that explicitly looks for hooks.
    try:
        from providers import get_provider
        prov = get_provider()
    except Exception as e:
        print(f"  {red('✗ provider:')} {e}", file=sys.stderr); return 1

    s = _aio.run(_db.get_settings(user_id)) or {}
    w = (s or {}).get("wizard", {}) or {}
    sender_name = (s.get("from_name") or w.get("company_name") or "the team").strip()
    sender_company = (w.get("company_name") or "our company").strip()
    sender_brief = (w.get("business_description") or "").strip()[:600]
    booking = (s.get("booking_url") or "").strip()
    tone = (args.tone or s.get("default_tone") or "friendly").strip().lower()
    tone_style = {
        "friendly": "casual and warm, like a colleague not a salesperson",
        "consultative": "professional but human, show industry knowledge",
        "broadcast": "confident and direct, premium positioning",
        "warm": "warm and personal, no corporate speak",
        "formal": "polite and professional, complete sentences",
    }.get(tone, "friendly")

    contact_name = (lead.get("contact_name") or "").strip()
    first_name = contact_name.split()[0] if contact_name else ""

    # Trim crawled text so the prompt stays under typical context windows.
    SNIPPET = text[:8000]
    prompt = (
        f"You are writing a cold opener for {sender_name} at {sender_company}.\n\n"
        f"WHAT WE DO:\n{sender_brief or 'Not provided.'}\n\n"
        f"PROSPECT:\n  org: {org}\n  contact: {contact_name or '(unknown)'}\n"
        f"  country: {lead.get('country','')}\n  context: {lead.get('event_name','')}\n\n"
        f"WHAT WE FOUND ON THEIR SITE (first {len(SNIPPET):,} chars of crawled text):\n"
        f"---\n{SNIPPET}\n---\n\n"
        "TASK:\n"
        "1. From the crawled text, pick ONE specific hook — something the prospect "
        "would think 'wow, they actually read my site'. Avoid generic compliments.\n"
        "2. Write a cold email that opens with that hook in the FIRST sentence.\n"
        f"3. Tone: {tone_style}.\n"
        "4. 90-130 words total. No bullet points, no markdown.\n"
        f"5. Greeting: '{('Hi ' + first_name) if first_name else 'Hi'},'.\n"
        f"6. End with: {('a booking link — ' + booking) if booking else 'a single soft question, no booking link.'}\n\n"
        'Return ONLY a JSON object: {"subject": "...", "body": "..."}\n'
        "No prose around the JSON, no markdown fences."
    )
    try:
        raw = prov.chat(
            messages=[
                {"role": "system", "content": "You are a top-tier cold-email writer. Return only JSON."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=600, temperature=0.6, timeout_s=45.0,
            response_format={"type": "json_object"},
        )
    except Exception as e:
        print(f"  {red('✗ AI call:')} {type(e).__name__}: {str(e)[:120]}", file=sys.stderr); return 1

    import json as _json
    try:
        data = _json.loads((raw or "").strip() or "{}")
        if not isinstance(data, dict) or not data.get("subject") or not data.get("body"):
            raise ValueError("missing subject/body")
    except Exception:
        # Fall back: try to find a JSON block inside.
        import re as _re
        m = _re.search(r"\{[\s\S]*\}", raw or "")
        if not m:
            print(f"  {red('✗ parse:')} couldn't extract JSON from AI reply.", file=sys.stderr); return 1
        try:
            data = _json.loads(m.group(0))
        except Exception:
            print(f"  {red('✗ parse:')} bad JSON from AI.", file=sys.stderr); return 1

    new_subject = (data.get("subject") or "").strip()[:160]
    new_body = (data.get("body") or "").strip()
    if not new_subject or not new_body:
        print(f"  {red('✗ AI returned empty draft.')}", file=sys.stderr); return 1

    # Persist + archive previous version into rewrite_history.
    now_iso = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc).isoformat()

    def _mut(_l, _new_s=new_subject, _new_b=new_body, _ts=now_iso, _tone=tone, _pages=n_crawled):
        prev_s = (_l.get("email_subject") or "").strip()
        prev_b = (_l.get("email_body") or "").strip()
        if prev_b and len(prev_b) > 30:
            # Stability fix (audit wave 26): dict.get(key, default)
            # only returns the default when the key is *absent* —
            # not when its stored value is None. Lead JSON columns
            # round-trip through SQLite/Postgres and can come back
            # as a literal None for "rewrite_history" once the row
            # exists, which then makes `hist.append(...)` raise
            # AttributeError, caught by the outer except as
            # "✗ persist:" — the rewrite is lost even though the AI
            # call + crawl succeeded.
            hist = _l.get("rewrite_history") or []
            hist.append({
                "date": _ts, "tone": _l.get("last_tone", "original"),
                "subject": prev_s, "body": prev_b,
                "linkedin": _l.get("linkedin_note", ""),
            })
            if len(hist) > 10:
                hist = hist[-10:]
            _l["rewrite_history"] = hist
        _l["email_subject"] = _new_s
        _l["email_body"] = _new_b
        _l["last_tone"] = _tone
        _l["_researched_at"] = _ts
        _l["_research_pages"] = _pages
        return _l

    try:
        _aio.run(_db.merge_lead(user_id, lid, _mut))
    except Exception as e:
        print(f"  {red('✗ persist:')} {e}", file=sys.stderr); return 1

    print(f"\n  {bold('new draft saved.')} preview:\n")
    print(f"    {dim('subject:')} {new_subject}")
    for line in new_body.splitlines()[:10]:
        print(f"    {dim(line)}")
    if len(new_body.splitlines()) > 10:
        print(f"    {dim('…')}")
    print(f"\n  {dim('original archived to rewrite_history. revert via the dashboard.')}\n")
    return 0


def cmd_lead(args: argparse.Namespace) -> int:
    """Print full detail for one lead, looked up by lead_id (or by
    a partial org_name match if --by-org is set).

    Example:
        huntova lead L3
        huntova lead "Aurora" --by-org
    """
    user_id = _bootstrap_local_env()
    if user_id is None:
        return 1
    import asyncio as _asyncio
    import db as _db
    target = (args.id_or_query or "").strip()
    if not target:
        print("[huntova] usage: huntova lead <id-or-org-name>", file=sys.stderr)
        return 1
    leads = _asyncio.run(_db.get_leads(user_id))
    if not leads:
        print("[huntova] no leads stored yet — run `huntova hunt` first.", file=sys.stderr)
        return 1
    if args.by_org:
        target_l = target.lower()
        matches = [l for l in leads if target_l in (l.get("org_name") or "").lower()]
    else:
        matches = [l for l in leads if (l.get("lead_id") or "") == target]
    if not matches:
        print(f"[huntova] no lead matched {target!r}.", file=sys.stderr)
        return 1
    if len(matches) > 1 and not args.first:
        print(f"[huntova] {len(matches)} leads match — re-run with --first or refine the query.", file=sys.stderr)
        for l in matches[:10]:
            print(f"  · {l.get('lead_id', '?')}  {l.get('org_name', '?')[:50]}", file=sys.stderr)
        return 1
    lead = matches[0]
    if args.format == "json":
        import json as _json
        print(_json.dumps(lead, indent=2, default=str))
        return 0
    # Human-readable detail block.
    color = sys.stdout.isatty()
    bold = (lambda s: f"\033[1m{s}\033[0m") if color else (lambda s: s)
    dim = (lambda s: f"\033[2m{s}\033[0m") if color else (lambda s: s)
    org = lead.get("org_name", "(unknown)")
    fit = lead.get("fit_score", "?")
    print(f"\n{bold(org)}  {dim(f'[{fit}/10]')}\n")
    field_groups = (
        ("Identity", ["lead_id", "org_website", "country", "city", "event_name"]),
        ("Scoring", ["fit_score", "buyability_score", "timing_score", "fit_rationale", "why_fit", "production_gap"]),
        ("Contact", ["contact_name", "contact_role", "contact_email", "contact_linkedin", "contact_phone", "org_linkedin"]),
        ("Outreach", ["email_subject", "email_body", "linkedin_note", "email_status", "notes"]),
    )
    for label, fields in field_groups:
        rows = [(f, lead.get(f)) for f in fields if lead.get(f)]
        if not rows:
            continue
        print(f"{bold(label)}")
        for f, v in rows:
            text = str(v)
            if len(text) > 200:
                text = text[:200] + dim(" …")
            print(f"  {f:18} {text}")
        print("")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """Export leads to CSV or JSON on stdout (or file via redirect)."""
    user_id = _bootstrap_local_env()
    if user_id is None:
        return 1
    import asyncio as _asyncio
    import db as _db
    leads = _asyncio.run(_db.get_leads(user_id))
    if not leads:
        print("[huntova] no leads to export.", file=sys.stderr)
        return 1
    if args.format == "json":
        import json as _json
        sys.stdout.write(_json.dumps(leads, indent=2, default=str, ensure_ascii=False))
        sys.stdout.write("\n")
        return 0
    # CSV — stable column order; use the unified csv builder when
    # available, else hand-build a minimal subset.
    import csv as _csv
    fields = [
        "lead_id", "org_name", "org_website", "country", "city",
        "fit_score", "why_fit", "production_gap", "event_name",
        "contact_name", "contact_role", "contact_email",
        "contact_linkedin", "org_linkedin",
        "email_subject", "email_body", "email_status", "notes",
        "created_at",
    ]
    w = _csv.writer(sys.stdout, quoting=_csv.QUOTE_MINIMAL)
    w.writerow(fields)
    for lead in leads:
        w.writerow([(lead.get(f) or "") for f in fields])
    return 0


_BASH_COMPLETION = r"""# Huntova bash completion. Install with:
#   huntova completion bash > ~/.local/share/bash-completion/completions/huntova
# or source it directly:
#   eval "$(huntova completion bash)"
_huntova_completion() {
    local cur prev words cword
    _init_completion -n : 2>/dev/null || {
        cur="${COMP_WORDS[COMP_CWORD]}"
        prev="${COMP_WORDS[COMP_CWORD-1]}"
    }
    local subcmds="serve hunt ls lead rm history export share init doctor update version completion"
    case "${prev}" in
        huntova)
            COMPREPLY=( $(compgen -W "${subcmds}" -- "${cur}") )
            return 0
            ;;
        --format)
            COMPREPLY=( $(compgen -W "table json csv text" -- "${cur}") )
            return 0
            ;;
        --countries)
            COMPREPLY=( $(compgen -W "Germany France Italy Spain UK USA Netherlands Sweden Denmark Norway" -- "${cur}") )
            return 0
            ;;
        completion)
            COMPREPLY=( $(compgen -W "bash zsh fish" -- "${cur}") )
            return 0
            ;;
    esac
    if [[ "${cur}" == --* ]]; then
        COMPREPLY=( $(compgen -W "--help --countries --max-leads --verbose --json --dry-run --limit --format --filter --by-org --first --top --title --yes --quick --port --host --no-browser --force" -- "${cur}") )
    fi
}
complete -F _huntova_completion huntova
"""

_ZSH_COMPLETION = r"""# Huntova zsh completion. Install with:
#   huntova completion zsh > ~/.zfunc/_huntova
#   echo 'fpath=(~/.zfunc $fpath)' >> ~/.zshrc
#   echo 'autoload -U compinit && compinit' >> ~/.zshrc
# or eval directly:
#   eval "$(huntova completion zsh)"
#compdef huntova
_huntova() {
    local context state state_descr line
    typeset -A opt_args
    _arguments -C \
        '1: :->cmd' \
        '*::arg:->args'
    case $state in
        cmd)
            local commands=(
                'serve:Boot the local dashboard'
                'hunt:One-shot headless hunt'
                'ls:List saved leads'
                'lead:Print full detail for one lead'
                'rm:Delete a lead permanently'
                'history:List recent hunt runs'
                'export:Export leads as CSV/JSON'
                'share:Mint a public share link'
                'init:Interactive first-run wizard'
                'doctor:Diagnostic + AI key probe'
                'update:Self-upgrade via pipx'
                'version:Print version'
                'completion:Print shell completion script'
            )
            _describe 'huntova command' commands
            ;;
        args)
            case $words[1] in
                hunt)
                    _arguments \
                        '--countries[Comma-separated country list]:countries:' \
                        '--max-leads[Stop after N leads]:N:' \
                        '--json[JSONL stream output]' \
                        '--dry-run[Walk setup without AI calls]' \
                        '--verbose[Show every log+thought event]'
                    ;;
                ls)
                    _arguments \
                        '--limit[Number of leads]:N:' \
                        '--filter[Substring or field:value filter]:filter:' \
                        '--format[Output format]:format:(table json)'
                    ;;
                lead)
                    _arguments \
                        '--by-org[Match by partial org name]' \
                        '--first[Pick first match if multiple]' \
                        '--format[Output format]:format:(text json)'
                    ;;
                rm)
                    _arguments '--yes[Skip confirmation]' '-y[Skip confirmation]'
                    ;;
                share)
                    _arguments \
                        '--top[How many leads]:N:' \
                        '--title[Public page title]:title:'
                    ;;
                completion)
                    _arguments '1:shell:(bash zsh fish)'
                    ;;
                doctor)
                    _arguments '--quick[Skip the network probe]'
                    ;;
                serve)
                    _arguments \
                        '--port[Bind port]:port:' \
                        '--host[Bind host]:host:' \
                        '--no-browser[Skip browser auto-open]'
                    ;;
            esac
            ;;
    esac
}
_huntova "$@"
"""

_FISH_COMPLETION = r"""# Huntova fish completion. Install with:
#   huntova completion fish > ~/.config/fish/completions/huntova.fish
complete -c huntova -f
complete -c huntova -n "__fish_use_subcommand" -a serve   -d "Boot the local dashboard"
complete -c huntova -n "__fish_use_subcommand" -a hunt    -d "One-shot headless hunt"
complete -c huntova -n "__fish_use_subcommand" -a ls      -d "List saved leads"
complete -c huntova -n "__fish_use_subcommand" -a lead    -d "Print full detail for one lead"
complete -c huntova -n "__fish_use_subcommand" -a rm      -d "Delete a lead permanently"
complete -c huntova -n "__fish_use_subcommand" -a history -d "List recent hunt runs"
complete -c huntova -n "__fish_use_subcommand" -a export  -d "Export leads as CSV/JSON"
complete -c huntova -n "__fish_use_subcommand" -a share   -d "Mint a public share link"
complete -c huntova -n "__fish_use_subcommand" -a init    -d "Interactive first-run wizard"
complete -c huntova -n "__fish_use_subcommand" -a doctor  -d "Diagnostic + AI key probe"
complete -c huntova -n "__fish_use_subcommand" -a update  -d "Self-upgrade via pipx"
complete -c huntova -n "__fish_use_subcommand" -a version -d "Print version"
complete -c huntova -n "__fish_seen_subcommand_from hunt" -l countries -d "Comma-separated country list"
complete -c huntova -n "__fish_seen_subcommand_from hunt" -l max-leads -d "Stop after N leads"
complete -c huntova -n "__fish_seen_subcommand_from hunt" -l json      -d "JSONL stream output"
complete -c huntova -n "__fish_seen_subcommand_from hunt" -l dry-run   -d "Walk setup, skip AI"
complete -c huntova -n "__fish_seen_subcommand_from hunt" -l verbose   -d "Show every event"
complete -c huntova -n "__fish_seen_subcommand_from ls"   -l filter    -d "Substring or field:value filter"
complete -c huntova -n "__fish_seen_subcommand_from ls"   -l limit     -d "Number of leads"
complete -c huntova -n "__fish_seen_subcommand_from ls"   -l format    -xa "table json"
complete -c huntova -n "__fish_seen_subcommand_from lead" -l by-org    -d "Match by partial org name"
complete -c huntova -n "__fish_seen_subcommand_from lead" -l first     -d "Pick first match if multiple"
complete -c huntova -n "__fish_seen_subcommand_from lead" -l format    -xa "text json"
complete -c huntova -n "__fish_seen_subcommand_from rm"   -l yes       -d "Skip confirmation"
complete -c huntova -n "__fish_seen_subcommand_from share" -l top      -d "How many top-fit leads"
complete -c huntova -n "__fish_seen_subcommand_from share" -l title    -d "Public page title"
complete -c huntova -n "__fish_seen_subcommand_from doctor" -l quick   -d "Skip network probe"
complete -c huntova -n "__fish_seen_subcommand_from completion" -a "bash zsh fish"
"""


def _completion_text(shell: str) -> str | None:
    """Return raw completion script for shell, or None if unsupported."""
    if shell == "bash":
        return _BASH_COMPLETION
    if shell == "zsh":
        return _ZSH_COMPLETION
    if shell == "fish":
        return _FISH_COMPLETION
    return None


def cmd_completion(args: argparse.Namespace) -> int:
    """Print shell completion code for bash, zsh, or fish."""
    shell = (args.shell or "bash").lower()
    text = _completion_text(shell)
    if text is None:
        print(f"[huntova] unknown shell {shell!r} — supported: bash, zsh, fish", file=sys.stderr)
        return 1
    sys.stdout.write(text)
    return 0


def _detect_shell() -> str | None:
    """Best-effort detect zsh/bash/fish from $SHELL."""
    sh = os.environ.get("SHELL", "")
    base = os.path.basename(sh).lower()
    if base in ("zsh", "bash", "fish"):
        return base
    return None


_RC_FENCE_OPEN = "# Added by `huntova install-completion`"
_RC_FENCE_CLOSE = "# End of `huntova install-completion` block"


def _ensure_rc_line(rc_path: Path, line: str, marker: str, dry_run: bool) -> bool:
    """Append `line` to rc_path if `marker` not already present. Returns
    True if changed; logs an error + returns False on permission errors
    (read-only rc, immutable home dir) so the caller can still tell the
    user what to add manually.
    """
    try:
        existing = rc_path.read_text() if rc_path.exists() else ""
    except OSError as e:
        print(f"[huntova] couldn't read {rc_path}: {e}", file=sys.stderr)
        return False
    if marker in existing:
        return False
    if dry_run:
        return True
    try:
        rc_path.parent.mkdir(parents=True, exist_ok=True)
        sep = "" if existing.endswith("\n") or not existing else "\n"
        # Write a CLOSED fence so --uninstall can find both edges and
        # only delete what we wrote, never user-authored lines that
        # happen to contain the same marker text. Round-10 audit
        # finding #1 caught the regression where `_remove_rc_block`
        # was eating user-installed `fpath+=(~/.zsh-completions)`
        # lines from prior tools.
        with rc_path.open("a", encoding="utf-8") as f:
            f.write(f"{sep}\n{_RC_FENCE_OPEN}\n{line}\n{_RC_FENCE_CLOSE}\n")
        return True
    except OSError as e:
        print(f"[huntova] couldn't patch {rc_path}: {e}", file=sys.stderr)
        print(f"           add this manually: {line}", file=sys.stderr)
        return False


def _remove_rc_block(rc_path: Path, marker: str, dry_run: bool) -> bool:
    """Strip ONLY the fenced block written by _ensure_rc_line.

    Round-10 audit finding #1: previous version stripped any line
    matching `marker` (e.g. `fpath+=(~/.zsh-completions)`), which is
    a generic shell idiom many users have in their .zshrc from prior
    tools. Now drops only lines between `_RC_FENCE_OPEN` and the
    matching `_RC_FENCE_CLOSE` (or the next blank line if the close
    fence is missing — backwards compat for v0.1.0a10 installs).
    """
    if not rc_path.exists():
        return False
    try:
        text = rc_path.read_text()
    except OSError as e:
        print(f"[huntova] couldn't read {rc_path}: {e}", file=sys.stderr)
        return False
    if _RC_FENCE_OPEN not in text:
        return False
    out_lines: list[str] = []
    in_block = False
    saw_close_fence = False
    for ln in text.splitlines(keepends=True):
        stripped = ln.strip()
        if stripped == _RC_FENCE_OPEN:
            in_block = True
            continue
        if in_block:
            if stripped == _RC_FENCE_CLOSE:
                in_block = False
                saw_close_fence = True
                continue
            # Backwards-compat: pre-fence-close installs (v0.1.0a10) had
            # NO close fence and the body was 1-2 lines (zsh wrote 2:
            # `fpath+=(~/.zsh-completions)` + `autoload -Uz compinit
            # && compinit`; bash wrote 1: `source ~/.bash_completion.d
            # /huntova`). Round-11 finding #1: previous a11 dropped
            # only ONE line, leaving the autoload orphaned. Now drop
            # the body line AND any non-blank line directly following
            # IF it looks like part of the same block (autoload, etc).
            if not saw_close_fence:
                # Drop this body line, and continue dropping until we
                # hit a blank line or the file end. v0.1.0a10's writes
                # were always followed by a blank, so this terminates
                # cleanly without eating user content past the block.
                if stripped == "":
                    in_block = False
                continue
        out_lines.append(ln)
    new_text = "".join(out_lines)
    if new_text == text:
        return False
    if not dry_run:
        try:
            rc_path.write_text(new_text)
        except OSError as e:
            print(f"[huntova] couldn't write {rc_path}: {e}", file=sys.stderr)
            return False
    return True


def cmd_install_completion(args: argparse.Namespace) -> int:
    """Auto-install (or uninstall) shell completion files, no eval, static files only."""
    from tui import bold, cyan, dim, green, red, yellow
    shell = (args.shell or _detect_shell() or "").lower()
    if shell not in ("bash", "zsh", "fish"):
        print(red("[huntova] could not detect shell — pass --shell {bash,zsh,fish}"), file=sys.stderr)
        return 1
    home = Path.home()
    if shell == "zsh":
        target = home / ".zsh-completions" / "_huntova"
        rc, rc_line, rc_marker = (home / ".zshrc",
                                   "fpath+=(~/.zsh-completions)\nautoload -Uz compinit && compinit",
                                   "fpath+=(~/.zsh-completions)")
    elif shell == "bash":
        target = home / ".bash_completion.d" / "huntova"
        rc, rc_line, rc_marker = (home / ".bashrc",
                                   f"[ -f {target} ] && source {target}",
                                   str(target))
    else:  # fish
        target = home / ".config" / "fish" / "completions" / "huntova.fish"
        rc = rc_line = rc_marker = None  # fish auto-loads

    verb = "uninstall" if args.uninstall else "install"
    prefix = dim("[dry-run]") + " " if args.dry_run else ""
    print(f"{prefix}{bold(verb.capitalize())} {cyan('huntova')} completion for {bold(shell)}")
    print(dim(f"  target: {target}"))

    if args.uninstall:
        if target.exists():
            if not args.dry_run:
                target.unlink()
            print(green(f"  removed {target}"))
        else:
            print(yellow(f"  skip   {target} (not present)"))
        if rc and _remove_rc_block(rc, rc_marker, args.dry_run):
            print(green(f"  cleaned {rc}"))
        elif rc:
            print(yellow(f"  skip   {rc} (no marker)"))
        return 0

    text = _completion_text(shell)
    if text is None:
        print(red(f"[huntova] no completion script for {shell}"), file=sys.stderr)
        return 1
    if target.exists() and not args.dry_run:
        print(yellow(f"  overwrite {target}"))
    if not args.dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    print(green(f"  wrote {target} ({len(text)} bytes)"))
    if rc:
        if _ensure_rc_line(rc, rc_line, rc_marker, args.dry_run):
            print(green(f"  patched {rc}"))
        else:
            print(dim(f"  ok     {rc} already configured"))
    print(dim("  restart your shell or `exec $SHELL` to activate"))
    return 0


_PLUGIN_TEMPLATE = '''"""Huntova plugin: {name}

Drop this file in ~/.config/huntova/plugins/ and run `huntova plugins`
to verify it's discovered. Then run `huntova hunt` and watch the
hooks fire.

See https://github.com/enzostrano/huntova/blob/master/plugins.py for
the full Protocol — every method is optional.
"""


class {cls_name}:
    name = "{name}"
    version = "0.1.0"

    def pre_search(self, ctx, queries):
        """Mutate the query list before SearXNG runs.

        Return the new list. Keep the original ones unless you really
        want to replace them.
        """
        return queries

    def post_save(self, ctx, lead):
        """Fire-and-forget side effects after a lead lands in the DB.

        Push to your CRM, ping Slack, append to a CSV, etc. Return
        value is ignored.
        """
        # Example: print every saved lead to a debug log
        # print(f"saved: {{lead.get('org_name')}}")
        return None
'''


_PLUGIN_REGISTRY_URL = os.environ.get(
    "HUNTOVA_PLUGIN_REGISTRY",
    "https://raw.githubusercontent.com/enzostrano/huntova-plugins/main/registry.json",
)


def _fetch_plugin_registry() -> list[dict]:
    """Fetch the static JSON registry of community plugins.

    The registry is a JSON list at a URL (default: GitHub-hosted in a
    `huntova-plugins` repo). Each entry is:
        {"name": "huntova-notion", "description": "...",
         "author": "...", "install": "pip install huntova-notion",
         "homepage": "https://...", "hooks": ["post_save"], "version": "..."}

    Cached for 1 hour at ~/.cache/huntova/plugin_registry.json.
    """
    import json as _json
    import time as _time
    import urllib.request

    cache_dir = Path(os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")) / "huntova"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / "plugin_registry.json"
    if cache.exists():
        try:
            age = _time.time() - cache.stat().st_mtime
            if age < 3600:
                return _json.loads(cache.read_text())
        except Exception:
            pass
    try:
        req = urllib.request.Request(_PLUGIN_REGISTRY_URL, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = _json.loads(r.read().decode("utf-8", errors="ignore"))
    except Exception:
        # Fall back to whatever cache exists (even stale)
        if cache.exists():
            try:
                return _json.loads(cache.read_text())
            except Exception:
                return []
        return []
    if not isinstance(data, list):
        return []
    try:
        cache.write_text(_json.dumps(data))
    except Exception:
        pass
    return data


def cmd_plugins(args: argparse.Namespace) -> int:
    """List discovered plugins, search the registry, or scaffold a new file."""
    os.environ.setdefault("APP_MODE", "local")
    _hydrate_env_from_local_config()
    if args.subcommand == "ls":
        args.subcommand = "list"
    if args.subcommand == "search":
        registry = _fetch_plugin_registry()
        if not registry:
            print("[huntova] couldn't reach the plugin registry.", file=sys.stderr)
            print(f"           tried: {_PLUGIN_REGISTRY_URL}", file=sys.stderr)
            print("           is your network up? Set HUNTOVA_PLUGIN_REGISTRY to point elsewhere.", file=sys.stderr)
            return 1
        q = (args.create_name or "").strip().lower()
        if q:
            registry = [
                p for p in registry
                if q in (p.get("name") or "").lower()
                or q in (p.get("description") or "").lower()
                or q in " ".join(p.get("hooks") or []).lower()
            ]
        if not registry:
            print(f"[huntova] no plugins matched {q!r}.")
            return 0
        if args.format == "json":
            import json as _json
            print(_json.dumps(registry, indent=2))
            return 0
        color = sys.stdout.isatty()
        bold = (lambda s: f"\033[1m{s}\033[0m") if color else (lambda s: s)
        dim = (lambda s: f"\033[2m{s}\033[0m") if color else (lambda s: s)
        green = (lambda s: f"\033[1;32m{s}\033[0m") if color else (lambda s: s)
        yellow = (lambda s: f"\033[1;33m{s}\033[0m") if color else (lambda s: s)
        print(f"\n{bold(f'{len(registry)} plugin(s) in registry:')}\n")
        # Verified plugins first.
        registry.sort(key=lambda p: (not bool(p.get("verified")), p.get("name") or ""))
        for p in registry:
            name = p.get("name", "?")
            ver = p.get("version", "?")
            desc = (p.get("description") or "").strip()
            author = p.get("author") or "?"
            hooks = ", ".join(p.get("hooks") or []) or "(no hooks declared)"
            install = p.get("install") or f"pip install {name}"
            home = p.get("homepage") or ""
            verified = bool(p.get("verified"))
            caps = p.get("capabilities") or []
            badge = green("✓ verified") if verified else yellow("○ community")
            print(f"  {bold(name)} {dim('v' + str(ver))}  {badge}  {dim('by ' + author)}")
            if desc:
                print(f"    {desc[:110]}")
            print(f"    {dim('hooks:')} {hooks}")
            if caps:
                cap_chips = " ".join(f"[{c}]" for c in caps)
                print(f"    {dim('caps: ')} {cap_chips}  {dim('— what this plugin can do')}")
            print(f"    {dim('install:')} {install}")
            if home:
                print(f"    {dim('home:')} {home}")
            print("")
        print(dim(f"  registry: {_PLUGIN_REGISTRY_URL}"))
        print(dim("  ✓ = reviewed by the Huntova core team. ○ = community-listed (install at your own risk)."))
        return 0
    if args.subcommand == "install":
        # One-command plugin install — looks up the plugin in the
        # registry, runs the install command (typically `pip install`),
        # then re-discovers so the user can immediately verify.
        target = (args.create_name or "").strip().lower()
        if not target:
            print("[huntova] usage: huntova plugins install <name>", file=sys.stderr)
            print("           run `huntova plugins search` to find one.", file=sys.stderr)
            return 1
        registry = _fetch_plugin_registry()
        if not registry:
            print("[huntova] couldn't reach the plugin registry — check network.", file=sys.stderr)
            return 1
        match = None
        for entry in registry:
            if isinstance(entry, dict) and entry.get("name", "").lower() == target:
                match = entry
                break
        if not match:
            # Allow exact match on partial too
            for entry in registry:
                if isinstance(entry, dict) and target in entry.get("name", "").lower():
                    match = entry
                    break
        if not match:
            print(f"[huntova] plugin {target!r} not found in the registry.", file=sys.stderr)
            print("           run `huntova plugins search` to list available ones.", file=sys.stderr)
            return 1
        install_cmd = (match.get("install") or "").strip()
        if not install_cmd or not install_cmd.startswith("pip install"):
            print(f"[huntova] plugin {match.get('name')!r} has a non-standard install: {install_cmd!r}", file=sys.stderr)
            print(f"           install it manually following: {match.get('homepage', '(no homepage)')}", file=sys.stderr)
            return 1
        # Capability + verified prompt before running the install
        caps = match.get("capabilities") or []
        verified = bool(match.get("verified"))
        color = sys.stdout.isatty()
        bold = (lambda s: f"\033[1m{s}\033[0m") if color else (lambda s: s)
        red = (lambda s: f"\033[31m{s}\033[0m") if color else (lambda s: s)
        print(f"\n  {bold('Plugin:')}        {match.get('name')}")
        print(f"  {bold('Description:')}  {(match.get('description') or '')[:200]}")
        print(f"  {bold('Hooks:')}        {', '.join(match.get('hooks') or [])}")
        if "secrets" in caps or "subprocess" in caps:
            print(f"  {bold('Capabilities:')} {red(', '.join(caps))}  ← REVIEW BEFORE INSTALLING")
        else:
            print(f"  {bold('Capabilities:')} {', '.join(caps) or '(none)'}")
        print(f"  {bold('Verified:')}     {'✓ reviewed by core team' if verified else '○ community-listed (install at your own risk)'}")
        print(f"  {bold('Homepage:')}     {match.get('homepage', '(none)')}")
        if not getattr(args, "force", False) and not verified:
            print(f"\n  {red('⚠')} community plugins run with full Python access on your machine.")
            print(f"  Re-run with --force to confirm you've reviewed the source:")
            print(f"    huntova plugins install {match.get('name')} --force\n")
            return 1
        # Run the install via pipx inject (best path) or pip --user
        import shutil, subprocess
        pkg = install_cmd.replace("pip install", "").strip().split()[0] if install_cmd.startswith("pip install") else install_cmd
        if shutil.which("pipx"):
            cmd = ["pipx", "inject", "huntova", pkg]
        else:
            cmd = [sys.executable, "-m", "pip", "install", "--user", pkg]
        print(f"\n  Running: {' '.join(cmd)}\n")
        try:
            rc = subprocess.call(cmd)
        except KeyboardInterrupt:
            return 130
        if rc != 0:
            print(f"\n[huntova] install failed (exit {rc}).", file=sys.stderr)
            return rc
        # Re-discover so the user can immediately confirm it loaded
        try:
            from plugins import get_registry, reset_for_tests
            try:
                reset_for_tests()
            except Exception:
                pass
            reg = get_registry()
            reg.discover()
            names = [p.get("name") if isinstance(p, dict) else getattr(p, "name", None)
                     for p in reg.list_plugins()]
            installed = next((n for n in names if n and target in n.lower()), None)
            if installed:
                print(f"\n  ✓ {installed} loaded successfully")
                print(f"  Run `huntova hunt` and the new hooks will fire.\n")
            else:
                print(f"\n  ⚠ install succeeded but plugin not auto-discovered yet.")
                print(f"  Try restarting any `huntova serve` session, or check entry_points.\n")
        except Exception as e:
            print(f"\n  (re-discovery probe failed: {type(e).__name__}: {e})\n")
        metrics_emit("cli_plugin_install", {"name": match.get("name"),
                                             "verified": verified,
                                             "caps": ",".join(caps)})
        return 0

    if args.subcommand == "contribute":
        # Open the GitHub PR template for the community plugin registry.
        # Shows the user the exact JSON shape they need to add and the
        # registry URL — even if they Ctrl-C out of the browser, the
        # printed link is enough to follow up later.
        registry_repo = "https://github.com/enzostrano/huntova-plugins"
        new_issue = f"{registry_repo}/issues/new?template=plugin-submission.md"
        color = sys.stdout.isatty()
        bold = (lambda s: f"\033[1m{s}\033[0m") if color else (lambda s: s)
        dim = (lambda s: f"\033[2m{s}\033[0m") if color else (lambda s: s)
        print(f"\n{bold('Submit your plugin to the public Huntova registry:')}\n")
        print(f"  1. Make sure your plugin is on PyPI:    {dim('pip install <your-plugin>')}")
        print(f"  2. Open a PR on the registry repo:")
        print(f"     {bold(registry_repo)}")
        print(f"  3. Add your entry to {dim('registry.json')} with this shape:\n")
        print(dim("     {"))
        print(dim('       "name": "huntova-yourthing",'))
        print(dim('       "description": "What it does — one short sentence.",'))
        print(dim('       "author": "your-name",'))
        print(dim('       "install": "pip install huntova-yourthing",'))
        print(dim('       "homepage": "https://github.com/you/huntova-yourthing",'))
        print(dim('       "hooks": ["post_save"],'))
        print(dim('       "capabilities": ["network"],'))
        print(dim('       "verified": false,'))
        print(dim('       "version": "0.1.0",'))
        print(dim('       "license": "MIT"'))
        print(dim("     }\n"))
        print(f"  4. CI runs basic schema + load checks. Maintainers review for")
        print(f"     verified ✓ status (real review) within 7 days.\n")
        print(f"  shortcut — open issue template in browser:")
        print(f"    {new_issue}\n")
        # Best-effort browser open (won't fail if no GUI / no `webbrowser` works)
        try:
            import webbrowser
            webbrowser.open_new_tab(new_issue)
        except Exception:
            pass
        return 0

    if args.subcommand == "create":
        new_name = (args.create_name or "").strip().lower().replace(" ", "-")
        if not new_name or not new_name.replace("-", "").replace("_", "").isalnum():
            print("[huntova] usage: huntova plugins create <name>  (kebab-case, e.g. crm-push)", file=sys.stderr)
            return 1
        plug_dir = _config_dir() / "plugins"
        plug_dir.mkdir(parents=True, exist_ok=True)
        target = plug_dir / f"{new_name.replace('-', '_')}.py"
        if target.exists() and not getattr(args, "force", False):
            print(f"[huntova] {target} already exists. Use --force to overwrite.", file=sys.stderr)
            return 1
        cls_name = "".join(p.capitalize() for p in new_name.replace("-", "_").split("_")) + "Plugin"
        target.write_text(_PLUGIN_TEMPLATE.format(name=new_name, cls_name=cls_name))
        print(f"[huntova] created {target}")
        print(f"           edit it, then run `huntova plugins` to verify discovery.")
        return 0
    from plugins import get_registry
    reg = get_registry()
    loaded = reg.discover()
    plugs = reg.list_plugins()
    if not plugs:
        print("[huntova] no plugins discovered.")
        print(f"           drop a *.py file in {_config_dir() / 'plugins'} to add one.")
        return 0
    if args.format == "json":
        import json as _json
        print(_json.dumps({"loaded": loaded, "plugins": plugs}, indent=2))
        return 0
    color = sys.stdout.isatty()
    bold = (lambda s: f"\033[1m{s}\033[0m") if color else (lambda s: s)
    dim = (lambda s: f"\033[2m{s}\033[0m") if color else (lambda s: s)
    print(f"\n{bold(f'{len(plugs)} plugin(s) loaded:')}\n")
    for p in plugs:
        hooks = ", ".join(p["hooks"]) or "(no hooks)"
        ver = p["version"]
        cls = p["class"]
        nm = p["name"]
        caps = p.get("capabilities") or []
        print(f"  {bold(nm)} {dim('v' + ver)}")
        print(f"    {dim('class:')} {cls}")
        print(f"    {dim('hooks:')} {hooks}")
        if caps:
            cap_chips = " ".join(f"[{c}]" for c in caps)
            print(f"    {dim('caps: ')} {cap_chips}")
        print("")
    errors = reg.errors()
    if errors:
        print(bold("⚠ load errors:"))
        for name, msg in errors:
            print(f"  · {name}: {msg}")
    return 0


def _build_adaptation_prompt(name: str, description: str, config: dict,
                              corpus: list[dict], outcomes: dict) -> str:
    """Compose the prompt sent to the AI for adaptation-card generation."""
    import json as _json
    countries = ", ".join(config.get("countries") or []) or "(default set)"
    return (
        f"Recipe name: {name}\n"
        f"Description: {description or '(none)'}\n"
        f"Countries: {countries}\n"
        f"Outcomes summary: {outcomes['total']} leads · "
        f"good={outcomes['feedback']['good']} bad={outcomes['feedback']['bad']} "
        f"sent={outcomes.get('sent_n', 0)} replied={outcomes.get('replied_n', 0)} "
        f"reply_rate_pct={outcomes['reply_rate_pct']}%\n\n"
        "Lead corpus (each row is one prospect we found and what happened to it):\n"
        f"{_json.dumps(corpus, indent=2, ensure_ascii=False)[:6000]}\n\n"
        "Produce an adaptation card as JSON. Identify:\n"
        "- overperforming_patterns: industries/signals/event-types that produced GOOD-fit + replied leads\n"
        "- weak_patterns: signals correlated with BAD-fit or no replies (to suppress)\n"
        "- winning_query_terms: 3-6 short query phrases the next hunt should boost\n"
        "- suppress_terms: 3-6 terms to filter OUT of future queries\n"
        "- recommended_query_additions: 2-4 new query strings to try next time\n"
        "- reply_correlated_signals: lead-level patterns that map to replies\n"
        "- scoring_rules: 0-6 lead-level score adjustments the post_score plugin can apply automatically. "
        "Each rule is {\"field\":\"<lead-field>\",\"op\":\"contains|eq|gt\",\"value\":<value>,\"delta\":<float -3..+3>}. "
        "Examples: {\"field\":\"tech_signals\",\"op\":\"contains\",\"value\":\"shopify\",\"delta\":1.5} "
        "or {\"field\":\"event_name\",\"op\":\"contains\",\"value\":\"hiring\",\"delta\":0.5}. "
        "Use rules ONLY for patterns clearly correlated with reply rate in the corpus.\n"
        "- summary: ONE sentence summarising what this recipe has learned\n\n"
        "Return ONLY a JSON object — no markdown, no prose. Each list 3-8 items."
    )


def cmd_recipe(args: argparse.Namespace) -> int:
    """Save / list / run / remove named hunt recipes.

    Examples:
        huntova recipe save agencies-eu --countries DE,FR,UK --max-leads 25
        huntova recipe ls
        huntova recipe run agencies-eu
        huntova recipe rm agencies-eu
    """
    user_id = _bootstrap_local_env()
    if user_id is None:
        return 1
    import asyncio as _asyncio
    import db as _db

    sub = (args.subcommand or "").lower()
    if sub == "ls":
        rows = _asyncio.run(_db.list_hunt_recipes(user_id))
        if not rows:
            print("[huntova] no recipes saved yet — use `huntova recipe save <name>` to make one.")
            return 0
        if args.format == "json":
            import json as _json
            print(_json.dumps(rows, indent=2, default=str))
            return 0
        color = sys.stdout.isatty()
        bold = (lambda s: f"\033[1m{s}\033[0m") if color else (lambda s: s)
        dim = (lambda s: f"\033[2m{s}\033[0m") if color else (lambda s: s)
        print(f"\n{bold(f'{len(rows)} recipe(s):')}\n")
        for r in rows:
            name = r.get("name", "?")
            run_count = r.get("run_count", 0)
            last_run = (r.get("last_run_at") or "")[:19].replace("T", " ") or "never"
            desc = (r.get("description") or "")[:60]
            print(f"  {bold(name):<30} {dim(f'runs={run_count} · last={last_run}')}")
            if desc:
                print(f"    {dim(desc)}")
        print("")
        return 0

    if sub == "save":
        name = (args.name or "").strip()
        if not name:
            print("[huntova] usage: huntova recipe save <name> [--countries ...] [--max-leads N]", file=sys.stderr)
            return 1
        config = {}
        if args.countries:
            config["countries"] = [c.strip() for c in args.countries.split(",") if c.strip()]
        if args.max_leads:
            config["max_leads"] = int(args.max_leads)
        if args.queries:
            config["queries"] = [q.strip() for q in args.queries.split("|") if q.strip()]
        rid = _asyncio.run(_db.save_hunt_recipe(user_id, name, config, args.description or ""))
        print(f"[huntova] saved recipe {name!r} (id={rid})")
        cnts = config.get("countries") or "(default countries)"
        print(f"           countries: {cnts}")
        if config.get("max_leads"):
            print(f"           max-leads: {config['max_leads']}")
        if config.get("queries"):
            print(f"           queries:   {len(config['queries'])} pre-set")
        print(f"           replay:    huntova recipe run {name}")
        return 0

    if sub == "adapt":
        name = (args.name or "").strip()
        if not name:
            print("[huntova] usage: huntova recipe adapt <name>", file=sys.stderr)
            return 1
        recipe = _asyncio.run(_db.get_hunt_recipe(user_id, name))
        if not recipe:
            print(f"[huntova] no recipe named {name!r}.", file=sys.stderr)
            return 1
        outcomes = _asyncio.run(_db.get_recipe_outcomes(user_id, name))
        threshold = 5 if args.force else 20
        if outcomes["total"] < threshold:
            print(f"[huntova] need at least {threshold} outcomes to generate an adaptation "
                  f"(this recipe has {outcomes['total']}). Use --force to override.", file=sys.stderr)
            return 1
        # Build the corpus for the AI: (lead, fit, status, feedback) tuples.
        from db_driver import get_driver
        drv = get_driver()
        conn = drv.get_conn()
        cur = conn.cursor()
        ids = outcomes["lead_ids"]
        ph = ", ".join(["%s"] * len(ids))
        cur.execute(drv.translate_sql(
            f"SELECT lead_id, data, fit_score, email_status FROM leads "
            f"WHERE user_id = %s AND lead_id IN ({ph})"
        ), [user_id] + list(ids))
        rows = [dict(r) for r in cur.fetchall()]
        # Pull feedback signals
        cur.execute(drv.translate_sql(
            f"SELECT lead_id, signal, reason FROM lead_feedback "
            f"WHERE user_id = %s AND lead_id IN ({ph})"
        ), [user_id] + list(ids))
        fb_map = {r[0]: (r[1], r[2]) for r in (cur.fetchall() or [])}
        drv.put_conn(conn)
        # Build a compact corpus the AI can reason over.
        import json as _json
        corpus = []
        for r in rows:
            try:
                data = _json.loads(r.get("data") or "{}")
            except Exception:
                data = {}
            sig, reason = fb_map.get(r["lead_id"], (None, None))
            corpus.append({
                "org": data.get("org_name", ""),
                "country": data.get("country", ""),
                "fit": int(r.get("fit_score") or 0),
                "why_fit": (data.get("why_fit") or "")[:160],
                "production_gap": (data.get("production_gap") or "")[:120],
                "event": (data.get("event_name") or data.get("event_type") or ""),
                "status": r.get("email_status", "new"),
                "feedback": sig,
                "feedback_reason": (reason or "")[:80],
            })
        cfg = recipe.get("config") or {}
        prompt = _build_adaptation_prompt(name, recipe.get("description", ""), cfg, corpus, outcomes)
        print(f"[huntova] generating adaptation for recipe {name!r} from {len(corpus)} outcomes…", file=sys.stderr)
        try:
            from providers import get_provider
            p = get_provider()
            response = p.chat(
                messages=[
                    {"role": "system", "content": "You are a precise sales-ops analyst. Given outcomes data, produce a structured adaptation card. Always reply with VALID JSON only — no prose, no markdown. Schema fields: 'overperforming_patterns' (list[str]), 'weak_patterns' (list[str]), 'winning_query_terms' (list[str]), 'suppress_terms' (list[str]), 'recommended_query_additions' (list[str]), 'reply_correlated_signals' (list[str]), 'scoring_rules' (list of {field,op,value,delta} dicts, 0-6 entries — see user prompt for shape), 'summary' (str, 1-2 sentences)."},
                    {"role": "user", "content": prompt},
                ],
                model=None,
                max_tokens=900,
                temperature=0.3,
                timeout_s=30.0,
                response_format={"type": "json_object"},
            )
        except Exception as e:
            print(f"[huntova] AI call failed: {e}", file=sys.stderr)
            return 1
        # Parse JSON out of the response
        adaptation = {}
        try:
            adaptation = _json.loads(response or "{}")
        except _json.JSONDecodeError:
            # Try to extract a JSON block
            import re as _re
            match = _re.search(r"\{[\s\S]*\}", response or "")
            if match:
                try:
                    adaptation = _json.loads(match.group(0))
                except Exception:
                    pass
        if not isinstance(adaptation, dict) or not adaptation:
            print("[huntova] AI returned non-JSON or empty adaptation. Try again.", file=sys.stderr)
            return 1
        adaptation["generated_from"] = {
            "outcomes_count": outcomes["total"],
            "feedback": outcomes["feedback"],
            "reply_rate_pct": outcomes["reply_rate_pct"],
        }
        _asyncio.run(_db.save_recipe_adaptation(user_id, name, adaptation))
        # Pretty print
        color = sys.stdout.isatty()
        bold = (lambda s: f"\033[1m{s}\033[0m") if color else (lambda s: s)
        green = (lambda s: f"\033[1;32m{s}\033[0m") if color else (lambda s: s)
        red = (lambda s: f"\033[1;31m{s}\033[0m") if color else (lambda s: s)
        dim = (lambda s: f"\033[2m{s}\033[0m") if color else (lambda s: s)
        print(f"\n{bold(f'Adaptation card for {name}')}")
        if adaptation.get("summary"):
            print(f"\n{adaptation['summary']}\n")
        for label, key, formatter in (
            ("overperforming patterns", "overperforming_patterns", green),
            ("weak patterns to suppress", "weak_patterns", red),
            ("winning query terms", "winning_query_terms", green),
            ("suppress terms", "suppress_terms", red),
            ("recommended query additions", "recommended_query_additions", green),
            ("reply-correlated signals", "reply_correlated_signals", green),
        ):
            items = adaptation.get(key) or []
            if not items:
                continue
            print(f"{bold(label)}")
            for item in items[:8]:
                marker = formatter("+") if formatter == green else formatter("-")
                print(f"  {marker} {str(item)[:140]}")
            print("")
        # scoring_rules render: each is a {field,op,value,delta} dict
        rules = adaptation.get("scoring_rules") or []
        if isinstance(rules, list) and rules:
            print(f"{bold('scoring rules (applied automatically by post_score plugin)')}")
            for rule in rules[:8]:
                if not isinstance(rule, dict):
                    continue
                field = rule.get("field", "?")
                op = rule.get("op", "contains")
                value = rule.get("value", "?")
                try:
                    delta = float(rule.get("delta") or 0.0)
                except (TypeError, ValueError):
                    delta = 0.0
                marker = green("+") if delta >= 0 else red("-")
                print(f"  {marker} {field} {op} {value!r} → {delta:+.1f}")
            print("")
        print(dim(f"saved · run again with `huntova recipe inspect {name}` to view alongside outcomes"))
        return 0

    if sub == "inspect":
        name = (args.name or "").strip()
        if not name:
            print("[huntova] usage: huntova recipe inspect <name>", file=sys.stderr)
            return 1
        recipe = _asyncio.run(_db.get_hunt_recipe(user_id, name))
        if not recipe:
            print(f"[huntova] no recipe named {name!r}.", file=sys.stderr)
            return 1
        outcomes = _asyncio.run(_db.get_recipe_outcomes(user_id, name))
        if args.format == "json":
            import json as _json
            print(_json.dumps({"recipe": recipe, "outcomes": outcomes},
                              indent=2, default=str))
            return 0
        color = sys.stdout.isatty()
        bold = (lambda s: f"\033[1m{s}\033[0m") if color else (lambda s: s)
        dim = (lambda s: f"\033[2m{s}\033[0m") if color else (lambda s: s)
        green = (lambda s: f"\033[1;32m{s}\033[0m") if color else (lambda s: s)
        red = (lambda s: f"\033[1;31m{s}\033[0m") if color else (lambda s: s)
        cfg = recipe.get("config") or {}
        run_count = recipe.get("run_count", 0)
        last_run = (recipe.get("last_run_at") or "never")[:10]
        print(f"\n{bold('Recipe: ' + name)}  {dim('(runs=' + str(run_count) + ', last=' + last_run + ')')}")
        if recipe.get("description"):
            print(f"{dim(recipe['description'][:120])}")
        print(f"\n{bold('config')}")
        for k in ("countries", "max_leads", "queries"):
            v = cfg.get(k)
            if v:
                vs = (", ".join(v) if isinstance(v, list) else str(v))
                print(f"  {k:14} {vs[:80]}")
        total = outcomes["total"]
        print(f"\n{bold('outcomes')}  {dim('(over ' + str(total) + ' leads from last run)')}")
        if outcomes["total"] == 0:
            print(dim("  no leads recorded yet — run the recipe first."))
            return 0
        fb = outcomes["feedback"]
        print(f"  {green('+ good')} {fb['good']:>3}   {red('- bad')} {fb['bad']:>3}   {dim('none ')}{fb['none']:>3}")
        bands = outcomes["fit_band"]
        print(f"  {dim('fit:')} high={bands['high']}  mid={bands['medium']}  low={bands['low']}")
        sent = outcomes.get("sent_n", 0)
        replied = outcomes.get("replied_n", 0)
        print(f"  {dim('outreach:')} sent={sent}  replied={replied}  reply_rate={outcomes['reply_rate_pct']}%")
        # Status histogram
        statuses = outcomes["status"]
        if statuses:
            print(f"\n{bold('status')}")
            for st, n in sorted(statuses.items(), key=lambda x: -x[1]):
                print(f"  {st:18} {n}")
        # Adaptation hint — bridge to v2.0 outcome-trained DNA. Today
        # we just suggest based on the data; future: AI-generated
        # adaptation card.
        if outcomes["total"] >= 5:
            hints = []
            if fb["good"] > fb["bad"] * 2 and fb["good"] >= 3:
                hints.append(f"{green('★')} this recipe is tuned — keep replaying")
            elif fb["bad"] > fb["good"] * 2 and fb["bad"] >= 3:
                hints.append(f"{red('!')} this recipe has weak fit — consider editing countries/queries")
            if outcomes["reply_rate_pct"] >= 20 and sent >= 5:
                hints.append(f"{green('★')} reply rate {outcomes['reply_rate_pct']}% is strong")
            elif sent >= 5 and outcomes["reply_rate_pct"] < 5:
                hints.append(f"{red('!')} low reply rate — emails may not be landing")
            if hints:
                print(f"\n{bold('signals')}")
                for h in hints:
                    print(f"  {h}")
        # AI-generated adaptation card if any
        adaptation = _asyncio.run(_db.get_recipe_adaptation(user_id, name))
        if adaptation and any(adaptation.get(k) for k in ("overperforming_patterns", "winning_query_terms", "summary")):
            print(f"\n{bold('adaptation card')}  {dim('(generated by AI from outcomes)')}")
            if adaptation.get("summary"):
                print(f"  {adaptation['summary']}")
            for key, label in (("overperforming_patterns", "+ overperforming"),
                                ("weak_patterns", "- weak"),
                                ("winning_query_terms", "+ winning queries"),
                                ("suppress_terms", "- suppress"),
                                ("recommended_query_additions", "+ try next"),
                                ("reply_correlated_signals", "+ reply signals")):
                items = adaptation.get(key) or []
                if not items:
                    continue
                print(f"  {dim(label)}: {', '.join(str(x) for x in items[:5])}")
            if adaptation.get("adaptation_at"):
                print(f"  {dim('generated ' + adaptation['adaptation_at'][:19])}")
        elif outcomes["total"] >= 20:
            print(f"\n{dim('tip: run `huntova recipe adapt ' + name + '` to AI-generate an adaptation card')}")
        return 0

    if sub == "rm":
        name = (args.name or "").strip()
        if not name:
            print("[huntova] usage: huntova recipe rm <name>", file=sys.stderr)
            return 1
        ok = _asyncio.run(_db.delete_hunt_recipe(user_id, name))
        if not ok:
            print(f"[huntova] no recipe named {name!r}.", file=sys.stderr)
            return 1
        print(f"[huntova] removed recipe {name!r}.")
        return 0

    if sub == "run":
        name = (args.name or "").strip()
        if not name:
            print("[huntova] usage: huntova recipe run <name>", file=sys.stderr)
            return 1
        recipe = _asyncio.run(_db.get_hunt_recipe(user_id, name))
        if not recipe:
            print(f"[huntova] no recipe named {name!r}.", file=sys.stderr)
            return 1
        cfg = recipe.get("config") or {}
        # Snapshot the recipe's previous lead-id set BEFORE the new run
        # so we can diff afterward.
        prior_ids = set(_asyncio.run(_db.get_recipe_last_lead_ids(user_id, name)))
        # Step 3 of v2.0 outcome-trained DNA: if the recipe has an
        # adaptation card, fold its recommended_query_additions into
        # the config.queries used by this run. Future iteration: a
        # built-in Huntova plugin reads adaptation from ctx.meta in
        # pre_search and rewrites the query list with boost/suppress
        # semantics. For today, additive query injection is the
        # simplest path.
        adaptation = _asyncio.run(_db.get_recipe_adaptation(user_id, name))
        merged_queries = list(cfg.get("queries") or [])
        applied_adaptation = False
        if adaptation and isinstance(adaptation, dict):
            new_qs = adaptation.get("recommended_query_additions") or []
            if isinstance(new_qs, list) and new_qs:
                # Dedupe + cap to 10 additions so we don't blow out the
                # batch size.
                seen = {q.lower() for q in merged_queries}
                for q in new_qs:
                    qs = str(q).strip()
                    if qs and qs.lower() not in seen:
                        merged_queries.append(qs)
                        seen.add(qs.lower())
                    if len(merged_queries) - len(cfg.get("queries") or []) >= 10:
                        break
                applied_adaptation = True
        # Build a synthetic args namespace for cmd_hunt. CLI `--max-leads`
        # takes precedence so a user can override the recipe's saved cap
        # for a one-off run without re-saving the recipe.
        countries_csv = ",".join(cfg.get("countries") or [])
        _cli_max = int(getattr(args, "max_leads", 0) or 0)
        _eff_max = _cli_max if _cli_max > 0 else int(cfg.get("max_leads") or 0)
        hunt_args = argparse.Namespace(
            countries=countries_csv,
            max_leads=_eff_max,
            verbose=False,
            json=False,
            dry_run=args.dry_run,
            from_share="",
        )
        # Stuff adaptation context onto an env var so the agent loop
        # (or plugins) can read it without changing cmd_hunt's
        # signature. `pre_search` plugins can read HV_RECIPE_ADAPTATION
        # JSON to apply boost/suppress logic.
        if applied_adaptation:
            import json as _json
            # Sanitise scoring_rules so the plugin gets a clean shape
            _raw_rules = adaptation.get("scoring_rules") or []
            _rules: list = []
            if isinstance(_raw_rules, list):
                for _r in _raw_rules:
                    if not isinstance(_r, dict):
                        continue
                    _f = _r.get("field"); _v = _r.get("value")
                    _o = (_r.get("op") or "contains").lower()
                    try:
                        _d = float(_r.get("delta") or 0.0)
                    except (TypeError, ValueError):
                        continue
                    if _f and _v is not None and _o in ("contains", "eq", "gt") and _d != 0.0:
                        _rules.append({"field": _f, "op": _o, "value": _v, "delta": _d})
            # Cap to 6 — the system prompt instructs the AI to emit 0-6
            # rules, but a hand-imported card or a hallucinating model
            # can blow past that. Without the cap, every per-lead score
            # eats N regex matches forever.
            if len(_rules) > 6:
                print(f"  {dim('warning: ' + str(len(_rules)) + ' rules provided, capping to 6')}",
                      file=sys.stderr)
                _rules = _rules[:6]
            os.environ["HV_RECIPE_ADAPTATION"] = _json.dumps({
                "recipe": name,
                "winning_terms": adaptation.get("winning_query_terms") or [],
                "suppress_terms": adaptation.get("suppress_terms") or [],
                "added_queries": adaptation.get("recommended_query_additions") or [],
                "scoring_rules": _rules,
            }, ensure_ascii=False, default=str)
        else:
            os.environ.pop("HV_RECIPE_ADAPTATION", None)
        _rules_n = len(adaptation.get("scoring_rules") or []) if applied_adaptation else 0
        _adds_n = len(adaptation.get("recommended_query_additions") or []) if applied_adaptation else 0
        adapt_msg = f" [+adaptation: {_adds_n} queries, {_rules_n} rules]" if applied_adaptation else ""
        print(f"[huntova] replaying recipe {name!r} (run #{(recipe.get('run_count') or 0) + 1}){adapt_msg}")
        rc = cmd_hunt(hunt_args)
        if args.dry_run:
            return rc
        # Real run completed. Compute diff: pull the user's full lead
        # set, take the recipe's-scope subset (countries match), and
        # compare to the prior snapshot.
        _asyncio.run(_db.touch_hunt_recipe(user_id, name))
        scope_countries = set((cfg.get("countries") or []))
        all_leads = _asyncio.run(_db.get_leads(user_id))
        if scope_countries:
            scoped = [l for l in all_leads if (l.get("country") or "") in scope_countries]
        else:
            scoped = all_leads
        new_ids = {str(l.get("lead_id")) for l in scoped if l.get("lead_id")}
        added = new_ids - prior_ids
        gone = prior_ids - new_ids
        kept = new_ids & prior_ids
        # Persist this run's snapshot for next time.
        _asyncio.run(_db.set_recipe_last_lead_ids(user_id, name, list(new_ids)))
        if not prior_ids:
            print(f"\n[huntova] recipe {name!r} — first run, {len(added)} leads recorded as baseline.")
            return rc
        color = sys.stdout.isatty()
        bold = (lambda s: f"\033[1m{s}\033[0m") if color else (lambda s: s)
        green = (lambda s: f"\033[1;32m{s}\033[0m") if color else (lambda s: s)
        red = (lambda s: f"\033[1;31m{s}\033[0m") if color else (lambda s: s)
        dim = (lambda s: f"\033[2m{s}\033[0m") if color else (lambda s: s)
        print(f"\n{bold(f'recipe diff vs last run:')}")
        print(f"  {green('+')} {len(added)} new")
        print(f"  {red('−')} {len(gone)} stale (no longer matching scope)")
        print(f"  {dim(f'· {len(kept)} carried over')}")
        # Sample up to 5 of each kind
        if added:
            print(f"\n{bold('New since last run:')}")
            id_to_lead = {str(l.get("lead_id")): l for l in scoped}
            for lid in list(added)[:5]:
                l = id_to_lead.get(lid, {})
                org = (l.get("org_name") or "(unknown)")[:40]
                fit = l.get("fit_score", "?")
                print(f"  {green('+')} [{fit}/10] {org}")
        if gone:
            print(f"\n{bold('Stale (in prior run, not now):')}")
            for lid in list(gone)[:5]:
                print(f"  {red('−')} {lid}")
        return rc

    if sub == "publish":
        # Kimi round-74 — gated by HV_RECIPE_URL_BETA on the SERVER side.
        # The CLI subcommand exists pre-launch but the server returns 404
        # unless the operator has flipped the flag.
        name = (args.name or "").strip()
        if not name:
            print("[huntova] usage: huntova recipe publish <name>", file=sys.stderr)
            return 1
        recipe = _asyncio.run(_db.get_hunt_recipe(user_id, name))
        if not recipe:
            print(f"[huntova] no recipe named {name!r}.", file=sys.stderr)
            return 1
        # Build the public payload — config + adaptation + plugin deps
        cfg = recipe.get("config") or {}
        adaptation = recipe.get("adaptation") or {}
        plugin_deps: list[str] = []
        try:
            from plugins import get_registry as _pg
            for p in _pg().list_plugins():
                pname = p.get("name") if isinstance(p, dict) else getattr(p, "name", None)
                if pname:
                    plugin_deps.append(pname)
        except Exception:
            pass
        public_payload = {
            "version": "1.0",
            "recipe": {
                "name": name,
                "description": recipe.get("description") or "",
                "countries": cfg.get("countries") or [],
                "queries": cfg.get("queries") or [],
                "max_leads": cfg.get("max_leads") or 0,
            },
            "adaptation": adaptation if isinstance(adaptation, dict) else {},
            "plugins": plugin_deps,
        }
        # POST to the server registered in HV_PUBLIC_URL
        server_url = os.environ.get("HV_PUBLIC_URL", "https://huntova.com").rstrip("/")
        endpoint = f"{server_url}/api/recipe/publish"
        try:
            import urllib.request
            import json as _json_pub
            body_bytes = _json_pub.dumps({
                "name": name,
                "description": recipe.get("description") or "",
                "recipe": public_payload["recipe"],
                "adaptation": public_payload["adaptation"],
                "plugins": public_payload["plugins"],
            }).encode("utf-8")
            req = urllib.request.Request(
                endpoint, data=body_bytes, method="POST",
                headers={"Content-Type": "application/json", "Accept": "application/json",
                         "User-Agent": f"huntova-cli/{VERSION}"},
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                resp = _json_pub.loads(r.read().decode("utf-8", errors="ignore") or "{}")
        except urllib.error.HTTPError as e:
            try:
                err = _json_pub.loads(e.read().decode("utf-8", errors="ignore") or "{}")
                msg = err.get("message") or err.get("error") or str(e.code)
            except Exception:
                msg = str(e.code)
            if e.code == 404:
                print(f"[huntova] {endpoint} returned 404 — public recipe URLs are gated by HV_RECIPE_URL_BETA on the server.", file=sys.stderr)
                print("           This feature isn't live yet on this Huntova server.", file=sys.stderr)
            else:
                print(f"[huntova] publish failed ({e.code}): {msg}", file=sys.stderr)
            return 1
        except Exception as e:
            print(f"[huntova] couldn't reach {endpoint}: {type(e).__name__}: {e}", file=sys.stderr)
            return 1
        if not resp.get("ok"):
            print(f"[huntova] server rejected publish: {resp.get('message') or resp.get('error') or 'unknown error'}", file=sys.stderr)
            return 1
        print(f"[huntova] recipe {name!r} published")
        print(f"          {resp.get('url')}")
        print(f"          fork it: huntova recipe import-url {resp.get('url')}.json")
        return 0

    if sub == "import-url":
        url = (args.name or "").strip()  # `name` slot reused for the URL
        if not url:
            print("[huntova] usage: huntova recipe import-url <https://.../r/<slug>>", file=sys.stderr)
            return 1
        # Accept both HTML and .json shapes. Splitting via urlparse so we
        # don't end up with `?ref=abc.json` when the user supplied a URL
        # with a query string — appending to the raw URL would mash the
        # extension into the query value.
        from urllib.parse import urlparse as _urlparse, urlunparse as _urlunparse
        _pu = _urlparse(url)
        if _pu.path.endswith(".json"):
            json_url = url
        else:
            json_url = _urlunparse(_pu._replace(path=_pu.path.rstrip("/") + ".json"))
        try:
            import urllib.request
            import json as _json_imp
            req = urllib.request.Request(json_url, headers={"Accept": "application/json",
                                                              "User-Agent": f"huntova-cli/{VERSION}"})
            with urllib.request.urlopen(req, timeout=15) as r:
                payload = _json_imp.loads(r.read().decode("utf-8", errors="ignore") or "{}")
        except Exception as e:
            print(f"[huntova] couldn't fetch {json_url}: {type(e).__name__}: {e}", file=sys.stderr)
            return 1
        if not isinstance(payload, dict) or "recipe" not in payload:
            print(f"[huntova] {json_url} didn't return a recipe payload (got {type(payload).__name__})", file=sys.stderr)
            return 1
        inner = payload.get("recipe") or {}
        rname = (inner.get("name") or "").strip()
        if not rname:
            print(f"[huntova] recipe payload had no name field — refusing to import without one", file=sys.stderr)
            return 1
        if not args.force:
            existing = _asyncio.run(_db.get_hunt_recipe(user_id, rname))
            if existing:
                print(f"[huntova] recipe {rname!r} already exists locally — re-run with --force to overwrite", file=sys.stderr)
                return 1
        # Save the recipe row + adaptation (if any) under the user
        cfg = {
            "countries": inner.get("countries") or [],
            "queries": inner.get("queries") or [],
            "max_leads": inner.get("max_leads") or 0,
        }
        _asyncio.run(_db.save_hunt_recipe(user_id, rname,
                                          description=inner.get("description") or "",
                                          config=cfg))
        adaptation = payload.get("adaptation") or {}
        if isinstance(adaptation, dict) and adaptation:
            try:
                _asyncio.run(_db.save_recipe_adaptation(user_id, rname, adaptation))
            except Exception as e:
                print(f"[huntova] (warning: couldn't import adaptation card: {e})", file=sys.stderr)
        plugin_deps = payload.get("plugins") if isinstance(payload, dict) else []
        if isinstance(plugin_deps, list) and plugin_deps:
            print(f"[huntova] recipe {rname!r} imported")
            print(f"          plugins it expects: {', '.join(str(p) for p in plugin_deps[:8])}")
            print(f"          install missing ones: huntova plugins create <name>")
        else:
            print(f"[huntova] recipe {rname!r} imported")
        print(f"          run it: huntova recipe run {rname}")
        return 0

    if sub == "export":
        return cmd_recipe_export(args, user_id)
    if sub == "import":
        return cmd_recipe_import(args, user_id)
    if sub == "diff":
        return cmd_recipe_diff(args, user_id)

    print(f"[huntova] unknown recipe subcommand {sub!r} — try ls / save / run / rm / inspect / adapt / publish / import-url / export / import / diff", file=sys.stderr)
    return 1


# ── Recipe export / import / diff (portable hunt configs) ──
# Recipe export/import — saved-hunt portability across machines —
# see NOTICE.md. Independent Python implementation; no source ported.

_RECIPE_SECRET_HINTS = (
    "password", "secret", "token", "webhook",
    # Round-7 audit finding #3: bare `apikey` (no underscore), camelCase
    # `accesstoken`, `auth`, `credential`, `pwd` were leaking through.
    # Now matches anywhere in the lowercased key.
    "apikey", "credential", "auth", "pwd",
)


def _is_secret_key(key: str) -> bool:
    """A key is a secret if it ends with _password/_key/key, or contains
    any of the secret-hint substrings (password / secret / token /
    webhook / apikey / credential / auth / pwd). Mirrors the same
    heuristic used elsewhere in cli.py."""
    k = (key or "").lower()
    if k.endswith("_password") or k.endswith("_key") or k.endswith("key") or k == "key":
        return True
    return any(h in k for h in _RECIPE_SECRET_HINTS)


def _strip_secrets(value):
    """Recursively drop every dict entry whose key looks like a secret."""
    if isinstance(value, dict):
        return {k: _strip_secrets(v) for k, v in value.items() if not _is_secret_key(k)}
    if isinstance(value, list):
        return [_strip_secrets(v) for v in value]
    return value


def _toml_value(v):
    """Minimal TOML scalar/list encoder. Returns a string, or None for
    `v is None` (callers must skip — TOML has no null literal).

    Round-7 audit finding #4: `None` was encoding as empty string `""`.
    Re-importing turned previously-unset wizard fields into deliberate
    empty strings, breaking export-then-import idempotency.
    """
    if v is None:
        return None
    if v is False:
        return "false"
    if v is True:
        return "true"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, list):
        items = [_toml_value(x) for x in v]
        return "[" + ", ".join(s for s in items if s is not None) + "]"
    if isinstance(v, dict):
        items = [(k, _toml_value(val)) for k, val in v.items()]
        return "{" + ", ".join(f"{k} = {s}" for k, s in items if s is not None) + "}"
    s = str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return '"' + s + '"'


def _toml_key(k: str) -> str:
    """Quote a TOML key if it contains anything beyond [A-Za-z0-9_-].
    Round-8 finding #2: dotted keys were corrupting the round-trip.
    Round-9 finding #6: control chars (\\b, \\f, \\n, \\r, NUL...)
    were emitting raw inside the quoted string, which tomllib then
    rejects on re-import with a confusing "Illegal character" error.
    Now escapes the full TOML basic-string set.
    """
    import re as _re
    if k and _re.fullmatch(r"[A-Za-z0-9_-]+", k):
        return k
    s = str(k)
    s = (s.replace("\\", "\\\\")
          .replace('"', '\\"')
          .replace("\b", "\\b")
          .replace("\f", "\\f")
          .replace("\n", "\\n")
          .replace("\r", "\\r")
          .replace("\t", "\\t"))
    # Strip any remaining U+0000–U+001F that TOML rejects, since the
    # user can't see them anyway and emitting raw breaks tomllib.
    s = "".join(ch for ch in s if ord(ch) >= 0x20 or ch in "\\")
    return '"' + s + '"'


def _toml_dump_section(name: str, data: dict) -> str:
    """Encode a dict as a TOML section. Nested dicts get emitted as
    `[name.key]` sub-tables instead of being silently dropped (round-7
    audit finding #1). Bare keys with `.` are quoted (round-8 finding #2).
    None values are skipped (TOML has no null literal).
    """
    if not data:
        return ""
    lines = [f"[{name}]"]
    nested: list[tuple[str, dict]] = []
    for k, v in data.items():
        if isinstance(v, dict) and v:
            nested.append((k, v))
            continue
        encoded = _toml_value(v)
        if encoded is not None:
            lines.append(f"{_toml_key(k)} = {encoded}")
    out = "\n".join(lines) + "\n"
    for k, sub in nested:
        out += "\n" + _toml_dump_section(f"{name}.{_toml_key(k)}", sub)
    return out


def cmd_recipe_export(args, user_id: int) -> int:
    """Dump current wizard + agent_dna + plugin config + preferred_provider
    to a portable TOML file. Strips secrets."""
    import asyncio as _asyncio
    import db as _db
    from datetime import datetime, timezone

    rname = ((getattr(args, "export_name", "") or args.name or "").strip()) or "default"
    settings = _asyncio.run(_db.get_settings(user_id)) or {}
    wizard = settings.get("wizard") or {}
    plugins = settings.get("plugins") or {}
    preferred = (settings.get("preferred_provider") or
                 os.environ.get("HV_AI_PROVIDER") or "anthropic")
    dna = _asyncio.run(_db.get_agent_dna(user_id)) or {}
    scoring_rules = (dna.get("scoring_rules") or
                     wizard.get("scoring_rules") or [])

    meta = {
        "name": rname,
        "exported_by": settings.get("exported_by") or "@local",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "huntova_version": VERSION,
        "preferred_provider": preferred,
    }
    body = []
    body.append(_toml_dump_section("meta", meta))
    body.append(_toml_dump_section("wizard", _strip_secrets(wizard)))
    body.append(_toml_dump_section("plugins", _strip_secrets(plugins)))
    # scoring_rules is a list of dicts → emit as [[scoring_rules]] tables
    if isinstance(scoring_rules, list) and scoring_rules:
        for rule in scoring_rules:
            if not isinstance(rule, dict):
                continue
            body.append("[[scoring_rules]]")
            for k, v in rule.items():
                if _is_secret_key(k):
                    continue
                body.append(f"{k} = {_toml_value(v)}")
            body.append("")
    text = "\n".join(b for b in body if b)

    out_path = (getattr(args, "out", "") or "").strip()
    if not out_path:
        from datetime import date
        out_path = str(Path.home() / f"huntova-{rname}-{date.today().isoformat()}.toml")
    p = Path(out_path).expanduser()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    except (OSError, PermissionError) as e:
        # Pre-a63 this raised an unhandled traceback. Now: friendly
        # error so the user knows whether it's a permission, missing
        # parent, or full-disk problem.
        print(f"[huntova] cannot write recipe to {p}: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    if os.name != "nt":
        try: os.chmod(p, 0o600)
        except OSError: pass
    print(f"[huntova] exported recipe {rname!r} → {p}")
    print(f"          share it: send the TOML; recipient runs `huntova recipe import {p.name}`")
    return 0


def _load_recipe_toml(path: str) -> dict | None:
    """Read & parse a recipe TOML. Returns None on failure (after printing)."""
    try:
        import tomllib
        return tomllib.loads(Path(path).expanduser().read_text())
    except FileNotFoundError:
        print(f"[huntova] no such file: {path}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"[huntova] couldn't parse {path}: {type(e).__name__}: {e}", file=sys.stderr)
        return None


def cmd_recipe_import(args, user_id: int) -> int:
    """Read a recipe TOML, merge into user_settings, regen agent_dna."""
    import asyncio as _asyncio
    import db as _db

    path = (args.name or "").strip()
    if not path:
        print("[huntova] usage: huntova recipe import <path.toml> [--force]", file=sys.stderr)
        return 1
    data = _load_recipe_toml(path)
    if data is None:
        return 1
    meta = data.get("meta") or {}
    rname = (meta.get("name") or "").strip()
    if not rname:
        print(f"[huntova] {path}: missing [meta].name — refusing to import", file=sys.stderr)
        return 1
    wiz_in = _strip_secrets(data.get("wizard") or {})
    plugins_in = _strip_secrets(data.get("plugins") or {})
    # Round-7 audit finding #5: presence-check, not truthiness — so a
    # deliberately-empty `[scoring_rules]` array CAN be imported as a
    # "clear my rules" signal instead of being silently ignored.
    has_rules = "scoring_rules" in data
    rules_in = data.get("scoring_rules") or []
    preferred_in = (meta.get("preferred_provider") or "").strip()

    def _mutator(current: dict) -> dict:
        cur_wiz = current.get("wizard") or {}
        cur_wiz.update(wiz_in)
        current["wizard"] = cur_wiz
        if plugins_in:
            cur_plug = current.get("plugins") or {}
            # Plugins config is typically nested ({slack: {webhook, channel}}).
            # A shallow update would replace the whole inner dict and drop
            # peer keys the caller didn't include — deep-merge instead so
            # partial updates only touch the keys they name.
            for _pk, _pv in plugins_in.items():
                if isinstance(_pv, dict) and isinstance(cur_plug.get(_pk), dict):
                    cur_plug[_pk] = {**cur_plug[_pk], **_pv}
                else:
                    cur_plug[_pk] = _pv
            current["plugins"] = cur_plug
        if preferred_in:
            current["preferred_provider"] = preferred_in
        if has_rules:
            current.setdefault("wizard", {})["scoring_rules"] = (
                rules_in if isinstance(rules_in, list) else []
            )
        return current

    # Round-7 audit finding #2: previous version made 4 separate
    # _asyncio.run() calls with no rollback between them. If
    # save_hunt_recipe failed AFTER merge_settings succeeded, the
    # wizard was already overwritten with no recipe row to point at,
    # leaving the user in a half-imported state with no error tying
    # back to "your wizard was changed". Now wrapped in a single
    # async coroutine; partial failures explicitly logged with a
    # remediation hint.
    async def _run_import() -> tuple[int, str]:
        # `args.force` may not exist on the import subparser if argparse
        # wiring missed it. Use getattr to keep the import flow alive
        # rather than crash with AttributeError on every first-time import.
        if not getattr(args, "force", False):
            existing = await _db.get_hunt_recipe(user_id, rname)
            if existing:
                return 1, f"recipe {rname!r} already exists locally — re-run with --force"
        await _db.merge_settings(user_id, _mutator)
        cfg = {
            "countries": wiz_in.get("countries") or wiz_in.get("default_countries") or [],
            "queries": wiz_in.get("queries") or [],
            "max_leads": wiz_in.get("max_leads") or 0,
        }
        try:
            await _db.save_hunt_recipe(
                user_id, rname,
                description=meta.get("description") or "", config=cfg)
        except Exception as e:
            return 2, (
                f"PARTIAL IMPORT: wizard updated but recipe row failed to save "
                f"({type(e).__name__}: {str(e)[:120]}). Your config has been "
                f"changed; run `huntova doctor` and consider `huntova recipe "
                f"export --name backup` to capture the current state."
            )
        # DNA regen is best-effort — settings + recipe row already persisted
        # so a Gemini timeout doesn't leave the system in a half-state.
        try:
            from app import generate_agent_dna  # type: ignore
            dna = generate_agent_dna(wiz_in)
            if isinstance(dna, dict) and dna:
                await _db.save_agent_dna(user_id, dna)
                return 0, f"recipe {rname!r} imported · agent_dna regenerated"
            return 0, f"recipe {rname!r} imported · DNA regen returned empty (run `huntova hunt` to retry)"
        except Exception as e:
            return 0, (f"recipe {rname!r} imported · DNA regen skipped "
                       f"({type(e).__name__}: {str(e)[:120]})")

    code, message = _asyncio.run(_run_import())
    if code == 1:
        print(f"[huntova] {message}", file=sys.stderr)
        return 1
    print(f"[huntova] {message}")
    if code == 0:
        print(f"          run it: huntova recipe run {rname}")
    return code


def cmd_recipe_diff(args, user_id: int) -> int:
    """Show what changes when importing this recipe over the current setup."""
    import asyncio as _asyncio
    import db as _db

    local_name = (args.name or "").strip()
    imported = (getattr(args, "name2", "") or "").strip()
    if not local_name or not imported:
        print("[huntova] usage: huntova recipe diff <local-recipe-name> <imported-path>", file=sys.stderr)
        return 1
    local = _asyncio.run(_db.get_hunt_recipe(user_id, local_name))
    if not local:
        print(f"[huntova] no local recipe named {local_name!r}", file=sys.stderr)
        return 1
    inc = _load_recipe_toml(imported)
    if inc is None:
        return 1
    settings = _asyncio.run(_db.get_settings(user_id)) or {}
    cur_provider = settings.get("preferred_provider") or os.environ.get("HV_AI_PROVIDER") or "anthropic"
    new_provider = (inc.get("meta") or {}).get("preferred_provider") or cur_provider
    meta = inc.get("meta") or {}
    print(f"\nrecipe diff: {local_name} ← {imported}")
    print(f"  exported_by={meta.get('exported_by','?')} · exported_at={meta.get('exported_at','?')}\n")

    def _show(label: str, cur: dict, new: dict):
        keys = sorted(set(cur) | set(new))
        if not any(cur.get(k) != new.get(k) for k in keys):
            return
        print(f"  {label}:")
        for k in keys:
            a, b = cur.get(k), new.get(k)
            if a == b:
                continue
            if k in cur: print(f"    - {k} = {a!r}")
            if k in new: print(f"    + {k} = {b!r}")
        print()

    _show("wizard", settings.get("wizard") or {}, inc.get("wizard") or {})
    _show("plugins", settings.get("plugins") or {}, inc.get("plugins") or {})
    if cur_provider != new_provider:
        print(f"  preferred_provider:\n    - {cur_provider}\n    + {new_provider}\n")
    print(f"apply with: huntova recipe import {imported} --force")
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    """Show recent agent runs from the local DB.

    Example:
        huntova history
        huntova history --limit 20 --format json
    """
    user_id = _bootstrap_local_env()
    if user_id is None:
        return 1
    import asyncio as _asyncio
    from db_driver import get_driver
    drv = get_driver()
    conn = drv.get_conn()
    sql = drv.translate_sql(
        "SELECT id, status, leads_found, ai_calls, queries_done, queries_total, "
        "started_at, ended_at FROM agent_runs WHERE user_id = %s "
        "ORDER BY started_at DESC LIMIT %s"
    )
    # try/finally so a SQL error doesn't leak the pool slot. Without
    # this, a malformed schema migration or driver hiccup would
    # permanently strand a Postgres pool connection (cloud mode);
    # SQLite uses the singleton conn so it's a no-op there but the
    # symmetry keeps the pattern consistent.
    try:
        cur = conn.cursor()
        cur.execute(sql, (user_id, max(1, int(args.limit))))
        rows_raw = cur.fetchall()
    finally:
        drv.put_conn(conn)
    if not rows_raw:
        print("[huntova] no hunt runs yet — run `huntova hunt` first.")
        return 0
    # Normalise to dicts (sqlite returns dicts via our row_factory; pg
    # returns RealDictRow which is also dict-like).
    rows = [dict(r) for r in rows_raw]
    if args.format == "json":
        import json as _json
        print(_json.dumps(rows, indent=2, default=str))
        return 0
    color = sys.stdout.isatty()
    bold = (lambda s: f"\033[1m{s}\033[0m") if color else (lambda s: s)
    dim = (lambda s: f"\033[2m{s}\033[0m") if color else (lambda s: s)
    print(f"\n{bold(f'{len(rows)} recent run(s):')}")
    print(f"  {dim('id      status      leads  queries     started')}")
    for r in rows:
        rid = str(r.get("id", "?"))[:7].ljust(7)
        status = str(r.get("status", "?"))[:10].ljust(10)
        leads = str(r.get("leads_found", "0")).rjust(5)
        q_done = r.get("queries_done") or 0
        q_total = r.get("queries_total") or 0
        queries = f"{q_done}/{q_total}".rjust(10)
        started = (r.get("started_at") or "")[:19].replace("T", " ")
        # Color-code status
        if color:
            if status.strip() in ("finished", "completed"):
                status = f"\033[1;32m{status}\033[0m"
            elif status.strip() in ("running", "queued"):
                status = f"\033[1;36m{status}\033[0m"
            elif status.strip() in ("error", "crashed"):
                status = f"\033[1;31m{status}\033[0m"
        print(f"  {rid} {status}  {leads}  {queries}  {dim(started)}")
    print("")
    return 0


def cmd_rm(args: argparse.Namespace) -> int:
    """Permanently delete a lead from local SQLite.

    Example:
        huntova rm L3
        huntova rm L3 --yes      # skip confirmation
    """
    user_id = _bootstrap_local_env()
    if user_id is None:
        return 1
    target = (args.lead_id or "").strip()
    if not target:
        print("[huntova] usage: huntova rm <lead_id>", file=sys.stderr)
        return 1
    import asyncio as _asyncio
    import db as _db
    lead = _asyncio.run(_db.get_lead(user_id, target))
    if not lead:
        print(f"[huntova] no lead {target!r} in local DB.", file=sys.stderr)
        return 1
    if not args.yes:
        org = lead.get("org_name", "?")
        try:
            confirm = input(f"Delete lead {target!r} ({org})? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\naborted.", file=sys.stderr)
            return 130
        if confirm not in ("y", "yes"):
            print("aborted.")
            return 1
    _asyncio.run(_db.permanent_delete_lead(user_id, target))
    print(f"[huntova] deleted {target} ({lead.get('org_name', '?')})")
    return 0


def cmd_share(args: argparse.Namespace) -> int:
    """Mint or query shareable lead URLs.

    Subcommands:
      mint (default)          Pick top-N leads, create /h/<slug> URL.
      status <slug>           Show view-count for an existing share.

    Examples:
        huntova share --top 10 --title "Q2 prospects"
        huntova share status abc12345
        huntova share abc12345        # treated as "share status abc12345"
    """
    sub = (args.subcommand or "mint").lower()
    # `huntova share <slug>` should query status — argparse no longer
    # rejects unknown verbs (we dropped `choices=`), so we recognise a
    # slug-shaped token here and rewrite the action.
    if sub not in ("mint", "status"):
        # The "subcommand" slot looks like a slug — treat as `status <slug>`.
        if not args.slug:
            args.slug = sub
        sub = "status"

    if sub == "status":
        slug = (args.slug or "").strip()
        if "/h/" in slug:
            slug = slug.rsplit("/h/", 1)[-1].split(".")[0].split("?")[0]
        if not slug:
            print("[huntova] usage: huntova share status <slug-or-url>", file=sys.stderr)
            return 1
        base = os.environ.get("HV_PUBLIC_URL", f"http://127.0.0.1:{DEFAULT_PORT}").rstrip("/")
        url = f"{base}/api/share/{slug}/views"
        try:
            import urllib.request
            import json as _json
            req = urllib.request.Request(url, headers={"Accept": "application/json",
                                                       "User-Agent": f"huntova-cli/{VERSION}"})
            with urllib.request.urlopen(req, timeout=10) as r:
                payload = _json.loads(r.read().decode("utf-8", errors="ignore") or "{}")
        except urllib.error.HTTPError as e:
            print(f"[huntova] {url} returned {e.code} — slug may not exist.", file=sys.stderr)
            return 1
        except Exception as e:
            print(f"[huntova] couldn't reach {url}: {type(e).__name__}: {e}", file=sys.stderr)
            return 1
        n = int(payload.get("views_30d") or 0)
        revoked = bool(payload.get("revoked"))
        share_url = f"{base}/h/{slug}"
        color = sys.stdout.isatty()
        bold = (lambda s: f"\033[1m{s}\033[0m") if color else (lambda s: s)
        green = (lambda s: f"\033[32m{s}\033[0m") if color else (lambda s: s)
        red = (lambda s: f"\033[31m{s}\033[0m") if color else (lambda s: s)
        dim = (lambda s: f"\033[2m{s}\033[0m") if color else (lambda s: s)
        print(f"\n  {bold('share status:')} {slug}")
        print(f"  {dim('url:')}     {share_url}")
        if revoked:
            print(f"  {dim('status:')}  {red('REVOKED')} (public link returns 410 — old view counts retained for audit)")
        print(f"  {dim('views:')}   {green(str(n))} (last 30 days, de-duped per IP+hour)")
        if n == 0:
            print(f"  {dim('(no clicks yet — share via cold-email, X, Slack, etc.)')}")
        return 0

    # Default: mint a new share
    user_id = _bootstrap_local_env()
    if user_id is None:
        return 1
    import asyncio as _asyncio
    import db as _db
    leads = _asyncio.run(_db.get_leads(user_id))
    if not leads:
        print("[huntova] no leads to share — run `huntova hunt` first.", file=sys.stderr)
        return 1
    # Sort by fit_score desc, take top-N.
    try:
        leads.sort(key=lambda l: int(l.get("fit_score") or 0), reverse=True)
    except Exception:
        pass
    top = leads[: max(1, int(args.top))]
    lead_ids = [str(l.get("lead_id") or "") for l in top if l.get("lead_id")]
    if not lead_ids:
        print("[huntova] picked leads have no IDs — DB integrity issue.", file=sys.stderr)
        return 1
    # Reuse the existing snapshot helper directly so we don't depend
    # on a running web server. db.create_hunt_share writes to the
    # hunt_shares table; the public /h/<slug> route reads from it.
    from server import _SHARE_LEAD_FIELDS, _sanitise_lead_for_share
    public_leads = [_sanitise_lead_for_share(l) for l in top]
    from datetime import datetime, timezone
    hunt_meta = {
        "leads_total": len(public_leads),
        "shared_at": datetime.now(timezone.utc).isoformat(),
    }
    slug = _asyncio.run(_db.create_hunt_share(
        user_id=user_id, run_id=None, leads=public_leads,
        hunt_meta=hunt_meta, title=(args.title or "")[:200],
    ))
    # Build the URL — point at the local server's default port unless
    # the user has overridden HV_PUBLIC_URL.
    base = os.environ.get("HV_PUBLIC_URL", f"http://127.0.0.1:{DEFAULT_PORT}").rstrip("/")
    url = f"{base}/h/{slug}"
    print(f"[huntova] shared {len(public_leads)} lead(s)")
    print(f"          {url}")
    print(f"          status: huntova share status {slug}")
    if base.startswith("http://127.0.0.1"):
        print("          (run `huntova serve` to make the link resolvable)")
    return 0


def _reach_proxy(lead: dict) -> str:
    """Approximate a reachability score for --explain-scores when the
    agent didn't emit one directly. Based on contact-path richness."""
    score = 0
    if lead.get("contact_email"):
        score += 5
    if lead.get("linkedin_url"):
        score += 3
    if lead.get("contact_name"):
        score += 2
    return f"~{score}"


def cmd_chat(args: argparse.Namespace) -> int:
    """Natural-language REPL on top of the existing huntova subcommands.

    Interactive TUI chat against your local Huntova install.
    The user types free-form prompts at a `>` prompt; we send each one to
    the configured AI provider in JSON mode with a strict schema, then
    dispatch the parsed action against the same code paths as `hunt` and
    `ls`. No new provider or transport — pure orchestration.

    Loop exits on Ctrl-C, `:q`, or `exit`. Up to ~10 turns of history are
    kept in-process so follow-ups like "now run it" have context.
    """
    os.environ.setdefault("APP_MODE", "local")
    _hydrate_env_from_local_config()

    try:
        from tui import bold, cyan, dim, green, red, yellow
    except Exception:  # tui import shouldn't fail, but degrade gracefully
        bold = cyan = dim = green = red = yellow = lambda s: s  # type: ignore

    # Provider gate — friendly hint if no key present, exit 1.
    try:
        from providers import get_provider, list_available_providers
    except Exception as e:
        print(f"[huntova] couldn't import providers: {e}", file=sys.stderr)
        return 1
    available = []
    try:
        available = list_available_providers() or []
    except Exception:
        available = []
    if not available:
        print(f"\n  {red('●')} No AI provider configured.")
        print(f"  Run {cyan('huntova onboard')} to add a key, then try again.\n")
        return 1
    try:
        provider = get_provider()
    except Exception as e:
        print(f"\n  {red('●')} Provider init failed: {e}")
        print(f"  Run {cyan('huntova onboard')} to fix.\n")
        return 1

    import json as _json

    SYSTEM_PROMPT = (
        "You are Huntova's command router. The user types natural language "
        "and you decide which CLI action to take. Reply with VALID JSON only "
        "(no prose, no markdown fences). One of these shapes:\n"
        '  {"action":"start_hunt","countries":["DE","FR"],'
        '"max_leads":10,"timeout_minutes":15,"icp":"video studios in Berlin"}\n'
        '  {"action":"list_leads","filter":"country:Germany"}\n'
        '  {"action":"research","lead_id":"L17","pages":14,"tone":""}\n'
        '  {"action":"sequence_run","dry_run":false,"max":25}\n'
        '  {"action":"sequence_status"}\n'
        '  {"action":"inbox_check"}\n'
        '  {"action":"pulse","since":"7d"}\n'
        '  {"action":"playbook_install","name":"solo-coach","force":false}\n'
        '  {"action":"playbook_ls"}\n'
        '  {"action":"answer","text":"…helpful reply for how-to / off-topic…"}\n'
        "Rules: countries is a list of ISO names or codes (DE, Germany, "
        "United Kingdom). max_leads default 10, cap 100. timeout_minutes "
        "default 15. icp is a short free-text description for the hunt. "
        "list_leads filter follows huntova ls --filter syntax: bare "
        'substring or "field:value" (org_name, country, city, '
        "contact_email, why_fit). research takes a single lead_id (the "
        "user must have mentioned a specific lead — never guess). "
        "playbook_install names: agencies-eu, b2b-saas-hiring, "
        "tech-recruiting, ecommerce-shopify, solo-coach, "
        "consultant-fractional, video-production, saas-developer-tools, "
        "design-studio, podcast-producer. When unsure or the user "
        "asks a how-to / definition / status question, use action=answer."
    )

    def _ask_ai(history: list[dict]) -> dict | None:
        """Send history to the AI and parse the JSON reply. Returns None
        on malformed JSON so the caller can prompt for a retry.

        Anthropic / Claude has no `response_format=json_object` like
        OpenAI does. Without help, Claude tends to return prose. We
        push it into JSON-mode by appending an assistant prefill of
        an open brace — Claude will then continue the JSON it started.
        Other providers ignore the prefill and use their native
        response_format.
        """
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, *history]
        # Anthropic-only JSON-mode trick: prefill the assistant turn
        # with an open brace so Claude completes the object. Anthropic
        # rejects two adjacent assistant blocks — strip any trailing
        # assistant turn from history (multi-turn case where Claude's
        # last reply is still in history) before adding the prefill.
        provider_name = (getattr(provider, "name", "") or "").lower()
        is_anthropic = "anthropic" in provider_name or "claude" in provider_name
        if is_anthropic:
            while messages and messages[-1].get("role") == "assistant":
                messages.pop()
            messages = [*messages, {"role": "assistant", "content": "{"}]
        try:
            raw = provider.chat(
                messages=messages,
                model=None,
                max_tokens=600,
                temperature=0.2,
                timeout_s=30.0,
                response_format={"type": "json_object"},
            )
        except Exception as e:
            print(f"  {red('●')} AI call failed: {e}")
            return None
        # Claude's reply is the continuation — re-add the leading brace
        # we prefilled. Other providers return a complete JSON string.
        if is_anthropic and raw and not raw.lstrip().startswith("{"):
            raw = "{" + raw
        try:
            parsed = _json.loads(raw or "{}")
        except _json.JSONDecodeError:
            import re as _re
            m = _re.search(r"\{[\s\S]*\}", raw or "")
            if not m:
                print(f"  {red('●')} AI returned non-JSON: {(raw or '')[:120]}")
                return None
            try:
                parsed = _json.loads(m.group(0))
            except Exception:
                print(f"  {red('●')} AI JSON malformed.")
                return None
        if not isinstance(parsed, dict):
            print(f"  {red('●')} AI reply was not a JSON object.")
            return None
        # Models occasionally return "Research" / "PULSE" instead of the
        # exact lowercase tag. Normalise so case variation isn't a false
        # rejection. Re-write the parsed dict so downstream dispatch sees
        # the canonical form.
        action = (parsed.get("action") or "").strip().lower()
        parsed["action"] = action
        _ALL = ("start_hunt", "list_leads", "answer", "research",
                "sequence_run", "sequence_status", "inbox_check",
                "pulse", "playbook_install", "playbook_ls")
        if action not in _ALL:
            print(f"  {red('●')} Unknown action {action!r} "
                  f"(expected one of: {', '.join(_ALL)}).")
            return None
        return parsed

    def _dispatch_hunt(parsed: dict) -> None:
        countries = parsed.get("countries") or []
        if not isinstance(countries, list):
            countries = []
        # Defensive sanitisation: cap to 30 entries (matches server-side
        # cap), truncate each to 50 chars, drop entries with control
        # characters or commas (would break the comma-joined --countries
        # flag downstream). Doesn't enforce a whitelist — the agent
        # accepts free-form geo names like "Northern California" or
        # "Benelux" — but does drop obvious garbage / hallucinations.
        _safe = []
        for c in countries[:30]:
            s = str(c).strip()[:50]
            if not s:
                continue
            if "," in s or any(ord(ch) < 32 for ch in s):
                continue
            _safe.append(s)
        countries = _safe
        try:
            max_leads = int(parsed.get("max_leads") or 10)
        except Exception:
            max_leads = 10
        max_leads = max(1, min(max_leads, 100))
        # AI may also specify a wall-clock cap. Clamp to a sane range
        # so a runaway prompt can't spawn a multi-hour hunt.
        timeout_minutes: int | None = None
        if parsed.get("timeout_minutes") is not None:
            try:
                timeout_minutes = int(parsed.get("timeout_minutes"))
                timeout_minutes = max(1, min(timeout_minutes, 120))
            except Exception:
                timeout_minutes = None
        icp = (parsed.get("icp") or "").strip()
        cmd_preview = ["huntova", "hunt"]
        if countries:
            cmd_preview += ["--countries", ",".join(countries)]
        cmd_preview += ["--max-leads", str(max_leads)]
        if timeout_minutes is not None:
            cmd_preview += ["--timeout-minutes", str(timeout_minutes)]
        print(f"\n  {green('▸')} {bold('start_hunt')}  {dim(' '.join(cmd_preview[1:]))}")
        if icp:
            print(f"  {dim('icp:')} {icp}")
        print()
        hunt_args = argparse.Namespace(
            countries=",".join(countries),
            max_leads=max_leads,
            timeout_minutes=timeout_minutes,
            verbose=False,
            json=False,
            dry_run=False,
            from_share="",
            explain_scores=False,
        )
        try:
            cmd_hunt(hunt_args)
        except KeyboardInterrupt:
            print(f"\n  {yellow('●')} hunt interrupted — back to chat.\n")

    def _dispatch_ls(parsed: dict) -> None:
        flt = (parsed.get("filter") or "").strip()
        print(f"\n  {green('▸')} {bold('list_leads')}  {dim('filter=' + (flt or '(none)'))}\n")
        ls_args = argparse.Namespace(
            limit=20,
            format="table",
            filter=flt,
        )
        try:
            cmd_ls(ls_args)
        except Exception as e:
            print(f"  {red('●')} list failed: {e}")

    # Banner
    print(f"\n  {bold('huntova chat')}  {dim('— natural-language CLI')}")
    print(f"  {dim('provider: ' + getattr(provider, 'name', '?') + '   exit: :q or Ctrl-C')}\n")

    history: list[dict] = []  # list of {role, content} — capped at 20 entries
    try:
        while True:
            try:
                user_text = input(f"{cyan('>')} ").strip()
            except EOFError:
                print()
                break
            if not user_text:
                continue
            if user_text.lower() in (":q", "exit", "quit", ":quit"):
                break
            history.append({"role": "user", "content": user_text})
            parsed = _ask_ai(history)
            if not parsed:
                # Drop the bad turn so retries don't keep poisoning context.
                history.pop()
                hint = 'try rephrasing — e.g. "find 10 video studios in Berlin"'
                print(f"  {dim(hint)}\n")
                continue
            history.append({"role": "assistant", "content": _json.dumps(parsed)})
            history = history[-20:]
            action = parsed.get("action")
            if action == "answer":
                text = (parsed.get("text") or "").strip()
                print(f"\n  {text}\n" if text else f"  {dim('(empty answer)')}\n")
            elif action == "start_hunt":
                _dispatch_hunt(parsed)
            elif action == "list_leads":
                _dispatch_ls(parsed)
            elif action == "research":
                lid = (parsed.get("lead_id") or "").strip()
                if not lid:
                    print(f"  {red('●')} research needs a lead_id — try `huntova research L17` "
                          f"or tell me which lead to research.\n")
                    continue
                _ns = argparse.Namespace(
                    lead_id=lid,
                    pages=int(parsed.get("pages") or 14),
                    tone=str(parsed.get("tone") or ""),
                )
                print(f"\n  {green('▸')} {bold('research')} {dim(lid)}\n")
                try:
                    cmd_research(_ns)
                except KeyboardInterrupt:
                    print(f"\n  {yellow('●')} research interrupted — back to chat.\n")
            elif action == "sequence_run":
                _ns = argparse.Namespace(
                    dry_run=bool(parsed.get("dry_run", False)),
                    max=str(parsed.get("max") or 25),
                )
                try:
                    import cli_sequence as _cs
                    _cs._cmd_run(_ns)
                except KeyboardInterrupt:
                    print(f"\n  {yellow('●')} sequence run interrupted.\n")
                except Exception as _e:
                    print(f"  {red('●')} sequence run failed: {_e}\n")
            elif action == "sequence_status":
                try:
                    import cli_sequence as _cs
                    _cs._cmd_status(argparse.Namespace())
                except Exception as _e:
                    print(f"  {red('●')} sequence status failed: {_e}\n")
            elif action == "inbox_check":
                try:
                    import cli_inbox as _ci
                    _ci._cmd_check(argparse.Namespace(since="14", dry_run=False))
                except Exception as _e:
                    print(f"  {red('●')} inbox check failed: {_e}\n")
            elif action == "pulse":
                try:
                    import cli_pulse as _cp
                    _cp._cmd_pulse(argparse.Namespace(
                        since=str(parsed.get("since") or "7d"),
                        json=False,
                    ))
                except Exception as _e:
                    print(f"  {red('●')} pulse failed: {_e}\n")
            elif action == "playbook_install":
                name = (parsed.get("name") or "").strip().lower()
                if not name:
                    print(f"  {red('●')} playbook_install needs a name. "
                          f"Try `huntova examples ls`.\n")
                    continue
                _ns = argparse.Namespace(
                    subcommand="install",
                    name=name,
                    force=bool(parsed.get("force", False)),
                )
                try:
                    cmd_examples(_ns)
                except Exception as _e:
                    print(f"  {red('●')} playbook install failed: {_e}\n")
            elif action == "playbook_ls":
                try:
                    cmd_examples(argparse.Namespace(subcommand="ls", name="", force=False))
                except Exception as _e:
                    print(f"  {red('●')} playbook ls failed: {_e}\n")
    except KeyboardInterrupt:
        print()
    print(f"\n  {dim('bye.')}\n")
    return 0


def cmd_hunt(args: argparse.Namespace) -> int:
    """Headless one-shot hunt — runs the agent in-process and streams
    formatted progress + leads to the terminal until the agent finishes.

    Example:
        huntova hunt --countries Germany,France --max-leads 10
        huntova hunt --json | jq          # machine-friendly output
        huntova hunt --dry-run            # walk setup, don't call AI
    """
    os.environ.setdefault("APP_MODE", "local")
    _hydrate_env_from_local_config()
    # Use the canonical provider resolver — it looks in env (any of the 13
    # supported providers), config.toml, and the OS keychain. The legacy
    # check only looked at HV_{GEMINI,ANTHROPIC,OPENAI}_KEY env vars and
    # would falsely reject users who'd configured Groq, DeepSeek, OpenRouter,
    # Mistral, Perplexity, Together, or any local server (Ollama, LM Studio,
    # llamafile).
    has_key = False
    try:
        from providers import list_available_providers
        has_key = bool(list_available_providers())
    except Exception:
        # Fall back to the env-only check if the provider module fails
        # to import for any reason — preserves the prior behavior.
        has_key = any(
            os.environ.get(v)
            for v in ("HV_ANTHROPIC_KEY", "HV_OPENAI_KEY", "HV_GEMINI_KEY")
        )
    # --dry-run skips the key requirement — pure wiring smoke test.
    if not has_key and not args.dry_run:
        print("[huntova] no API key configured. Run `huntova onboard` first.", file=sys.stderr)
        return 2

    countries = [c.strip() for c in (args.countries or "").split(",") if c.strip()]
    fork_title = ""
    # Stability fix (audit wave 26): json_mode + info were bound at
    # line ~5658, AFTER the --from-share preflight. The preflight
    # used a local `info = print` (stdout), so `huntova hunt --json
    # --from-share <slug>` emitted human-readable lines like
    # `[huntova] forking share 'abc'` to stdout BEFORE the JSON
    # stream began — `jq` then choked on the first non-JSON line.
    # Bind json_mode + info early so the preflight respects
    # --json from the start.
    json_mode = bool(args.json)
    info = (lambda *a, **k: print(*a, **k, file=sys.stderr)) if json_mode else print
    # --from-share <slug-or-url>: pull the public snapshot and adopt
    # its country set as the new hunt's geo. Future iterations can
    # also adopt the original queries / ICP once shares store config.
    if args.from_share:
        slug = args.from_share.strip()
        # Accept full URL or bare slug
        if "/h/" in slug:
            slug = slug.rsplit("/h/", 1)[-1].split(".")[0].split("?")[0].split("#")[0]
        host = os.environ.get("HV_PUBLIC_URL", f"http://127.0.0.1:{DEFAULT_PORT}").rstrip("/")
        url = f"{host}/h/{slug}.json"
        try:
            import urllib.request
            import json as _json_pre
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=8) as r:
                payload = _json_pre.loads(r.read().decode("utf-8", errors="ignore"))
        except Exception as e:
            print(f"[huntova] couldn't fetch share {slug!r} from {url}: {e}", file=sys.stderr)
            print("           is `huntova serve` running on the same machine that minted the slug?", file=sys.stderr)
            return 1
        share = (payload or {}).get("share") or {}
        forked_leads = share.get("leads") or []
        seen_countries = []
        for ld in forked_leads:
            c = (ld.get("country") or "").strip()
            if c and c not in seen_countries:
                seen_countries.append(c)
        if not countries and seen_countries:
            countries = seen_countries
        fork_title = (share.get("title") or "")[:120]
        info(f"[huntova] forking share {slug!r}{' — ' + fork_title if fork_title else ''}")
        info(f"[huntova] adopted countries from share: {', '.join(seen_countries) or '(none)'}")
    if not countries:
        # Sensible default that matches `launchHuntQuickStart` in the dashboard.
        countries = ["United Kingdom", "USA", "Germany", "France", "Spain", "Italy"]

    import asyncio as _asyncio
    import json as _json
    import time as _time

    # Init DB so the agent can write leads. Done before importing
    # agent_runner so the schema is in place when the thread starts.
    import db as _db
    _db.init_db_sync()
    from auth import _ensure_local_user
    user = _asyncio.run(_ensure_local_user())
    user_id = user["id"]

    # json_mode + info were already bound earlier (above the
    # --from-share preflight) so that block also routes correctly.

    if args.dry_run:
        # Walk the same setup path as a real run but stop before
        # spawning the agent thread. Confirms imports + DB + user
        # bootstrap + countries validation + provider resolution.
        info(f"[huntova] dry-run: APP_MODE={os.environ.get('APP_MODE')}")
        info(f"[huntova] dry-run: storage={'PostgreSQL' if os.environ.get('DATABASE_URL') else 'SQLite'}")
        info(f"[huntova] dry-run: user_id={user_id} email={user['email']}")
        info(f"[huntova] dry-run: countries={countries}")
        info(f"[huntova] dry-run: max_leads={args.max_leads or 'unbounded'}")
        try:
            from providers import list_available_providers
            info(f"[huntova] dry-run: providers={list_available_providers() or '(none)'}")
        except Exception as e:
            info(f"[huntova] dry-run: providers probe failed: {e}")
        if json_mode:
            print(_json.dumps({"event": "dry_run_ok", "user_id": user_id, "countries": countries}))
        else:
            info("[huntova] dry-run: ✓ wiring intact, agent NOT started")
        return 0

    # Hand-roll a quick subscriber that prints events as they arrive.
    # Don't reuse the SSE EventSource path — that's HTTP-bound. We tap
    # the bus directly.
    from user_context import get_or_create_context
    from agent_runner import agent_runner
    ctx = get_or_create_context(user_id, user["email"], user.get("tier", "local"))
    sub_q = ctx.bus.subscribe()

    leads_seen: list[dict] = []
    stop_flag = {"stop": False}

    def _emit_json(obj: dict) -> None:
        # Use sys.stdout.buffer with explicit UTF-8 encoding so leads with
        # accented chars ("Müller", "François") or emojis don't crash
        # `huntova hunt --json` on platforms whose stdout encoding isn't
        # UTF-8 (Windows cmd.exe being the prime culprit).
        line = (_json.dumps(obj, default=str, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            sys.stdout.buffer.write(line)
        except AttributeError:
            # Fallback if stdout is wrapped in a non-buffer-bearing stream
            # (rare — pytest capture, some CI runners).
            sys.stdout.write(line.decode("utf-8", errors="replace"))
        sys.stdout.flush()

    def _consume():
        # Each frame is "event: NAME\ndata: JSON\n\n". Parse and print.
        last_print = 0.0
        while not stop_flag["stop"]:
            try:
                msg = sub_q.get(timeout=0.5)
            except Exception:
                continue
            if not msg:
                continue
            try:
                lines = msg.split("\n")
                event_name = ""
                data_json = ""
                for ln in lines:
                    if ln.startswith("event: "):
                        event_name = ln[len("event: "):].strip()
                    elif ln.startswith("data: "):
                        data_json = ln[len("data: "):]
                d = _json.loads(data_json) if data_json else {}
            except Exception:
                continue
            if event_name == "lead":
                lead = d if isinstance(d, dict) else {}
                leads_seen.append(lead)
                if json_mode:
                    _emit_json({"event": "lead", **lead})
                else:
                    org = lead.get("org_name", "?")[:40]
                    fit = lead.get("fit_score", "?")
                    country = lead.get("country", "")
                    website = lead.get("org_website", lead.get("url", ""))
                    print(f"  ✓ [{fit}/10] {org}  · {country}  · {website[:60]}")
            elif event_name == "status":
                state = d.get("state", "")
                msg_text = d.get("message") or d.get("text") or ""
                if state in ("running", "queued"):
                    if json_mode:
                        _emit_json({"event": "status", "state": state, "message": msg_text})
                    else:
                        now = _time.time()
                        if now - last_print > 5.0:
                            print(f"  · {msg_text or state}")
                            last_print = now
                elif state in ("done", "completed", "stopped", "exhausted", "error", "idle"):
                    if json_mode:
                        _emit_json({"event": "status", "state": state, "message": msg_text, "final": True})
                    else:
                        print(f"\n[huntova] hunt {state}: {msg_text}")
                    stop_flag["stop"] = True
                    break
            elif event_name == "log" and (args.verbose or json_mode):
                if json_mode:
                    _emit_json({"event": "log", "level": d.get("level", "info"), "msg": d.get("msg", "")})
                else:
                    level = d.get("level", "info")
                    m = d.get("msg", "")
                    print(f"  [{level}] {m[:120]}")
            elif event_name == "thought" and (args.verbose or json_mode):
                if json_mode:
                    _emit_json({"event": "thought", "msg": d.get("msg", "")})
                else:
                    print(f"  💭 {(d.get('msg') or '')[:120]}")
            # Honor user-set max-leads cap by stopping the agent
            if args.max_leads and len(leads_seen) >= args.max_leads:
                if not json_mode:
                    print(f"\n[huntova] max-leads={args.max_leads} reached, stopping")
                agent_runner.stop_agent(user_id)
                stop_flag["stop"] = True
                break

    # Start the agent thread.
    _agent_config: dict = {"countries": countries}
    if getattr(args, "max_leads", None):
        _agent_config["max_leads"] = args.max_leads
    _timeout_minutes = getattr(args, "timeout_minutes", None)
    if _timeout_minutes:
        _agent_config["timeout_minutes"] = _timeout_minutes
    try:
        result = _asyncio.run(agent_runner.start_agent(
            user_id=user_id,
            user_email=user["email"],
            user_tier=user.get("tier", "local"),
            config=_agent_config,
        ))
    except Exception as e:
        print(f"[huntova] failed to start agent: {e}", file=sys.stderr)
        return 1
    if not result.get("ok"):
        print(f"[huntova] agent refused to start: {result.get('error')}", file=sys.stderr)
        return 1

    if not json_mode:
        info(f"[huntova] hunting in {len(countries)} countries: {', '.join(countries)}")
        if args.max_leads:
            info(f"[huntova] cap: {args.max_leads} leads")
        info(f"[huntova] streaming to ~/.local/share/huntova/db.sqlite (Ctrl-C to stop)\n")
    else:
        _emit_json({"event": "start", "countries": countries, "max_leads": args.max_leads or None})

    try:
        _consume()
    except KeyboardInterrupt:
        info("\n[huntova] interrupted — stopping agent")
        agent_runner.stop_agent(user_id)
        stop_flag["stop"] = True
    finally:
        try:
            ctx.bus.unsubscribe(sub_q)
        except Exception:
            pass

    if json_mode:
        _emit_json({"event": "summary", "leads_saved": len(leads_seen)})
        metrics_emit("cli_hunt", {"lead_count": len(leads_seen),
                                   "country_count": len(countries),
                                   "from_share": bool(args.from_share),
                                   "json_mode": True})
        return 0
    info(f"\n[huntova] {len(leads_seen)} leads saved.")
    if leads_seen:
        # Sort top 5 by fit score for the summary.
        try:
            ranked = sorted(leads_seen, key=lambda l: int(l.get("fit_score") or 0), reverse=True)[:5]
        except Exception:
            ranked = leads_seen[:5]
        info(f"[huntova] top {len(ranked)}:")
        for lead in ranked:
            org = lead.get("org_name", "?")[:40]
            fit = lead.get("fit_score", "?")
            why = (lead.get("why_fit") or "")[:80]
            if args.explain_scores:
                # Kimi round-76 hot-fix: surface the score breakdown so
                # skeptics see how the agent reaches each score. Only
                # the AI-emitted dimensions are real; reach (which the
                # agent doesn't directly score) is approximated from
                # contact + LinkedIn presence.
                buy = lead.get("buyability_score", "?")
                tim = lead.get("timing_score", "?")
                reach = lead.get("reachability_score") or _reach_proxy(lead)
                trace = lead.get("_score_trace") or []
                trace_suffix = ""
                if isinstance(trace, list) and trace:
                    trace_suffix = f"  [{', '.join(str(t)[:40] for t in trace[:3])}]"
                info(f"  · [{fit}/10] {org}  — {why}")
                info(f"      fit={fit} · buy={buy} · timing={tim} · reach={reach}{trace_suffix}")
            else:
                info(f"  · [{fit}/10] {org}  — {why}")
    info(f"\n[huntova] view in dashboard: `huntova serve`")
    if not args.explain_scores and any(l.get("fit_score") for l in leads_seen):
        info(f"[huntova] (rerun with --explain-scores to see fit/buy/timing/reach breakdown)")
    metrics_emit("cli_hunt", {"lead_count": len(leads_seen),
                               "country_count": len(countries),
                               "from_share": bool(args.from_share),
                               "json_mode": False})
    return 0


_BUNDLED_EXAMPLES = {
    "agencies-eu": {
        "description": "Boutique creative + marketing agencies in Europe hiring video / motion staff",
        "config": {
            "countries": ["Germany", "France", "United Kingdom", "Spain", "Italy"],
            "queries": [
                "boutique creative agency Berlin hiring motion designer",
                "marketing agency Paris hiring video editor",
                "creative studio London 10 employees hiring",
                "motion design agency Madrid B2B clients",
                "production agency Milan hiring junior editor",
                "branding agency Berlin scaling team",
                "indie creative studio London open positions",
                "video production agency Amsterdam hiring",
            ],
            "max_leads": 25,
        },
    },
    "b2b-saas-hiring": {
        "description": "Seed/Series A B2B SaaS companies hiring SDRs or Account Executives",
        "config": {
            "countries": ["USA", "United Kingdom"],
            "queries": [
                "B2B SaaS startup hiring SDR Series A",
                "B2B SaaS company hiring account executive 10-50 employees",
                "seed-stage SaaS hiring outbound sales rep",
                "vertical SaaS founder hiring first sales hire",
                "Series A SaaS scaling go-to-market team",
                "B2B platform hiring sales development representative",
                "SaaS company recently funded hiring sales",
                "early-stage SaaS hiring account executive remote",
            ],
            "max_leads": 25,
        },
    },
    "tech-recruiting": {
        "description": "Technical recruiting + DevOps staffing agencies in US/UK",
        "config": {
            "countries": ["USA", "United Kingdom"],
            "queries": [
                "technical recruiting agency UK 10-25 employees",
                "DevOps staffing firm USA hiring recruiter",
                "embedded engineering recruiter UK clients",
                "fintech technical recruiting agency US",
                "machine learning talent firm UK",
                "Python recruiter agency USA boutique",
                "platform engineer staffing agency",
                "site reliability engineering recruiter UK",
            ],
            "max_leads": 25,
        },
    },
    "ecommerce-shopify": {
        "description": "Series A+ Shopify-stack ecommerce brands with marketing leadership openings",
        "config": {
            "countries": ["USA", "United Kingdom", "Germany"],
            "queries": [
                "Shopify Plus brand hiring head of growth Series A",
                "DTC ecommerce brand hiring CMO 10-50 employees",
                "Shopify Plus retailer hiring performance marketer",
                "Series A DTC brand scaling acquisition team",
                "ecommerce founder hiring head of marketing",
                "DTC brand recently funded hiring CMO",
                "Shopify ecommerce hiring growth lead",
                "B2C brand hiring senior brand manager",
            ],
            "max_leads": 25,
        },
    },
    # ── playbook expansion (a115) ──────────────────────────────────
    # Each entry below is a "playbook" — a recipe + ICP brief + tone
    # hint. `huntova playbook install <name>` saves it AND seeds the
    # wizard's `business_description` + `target_clients` so the agent
    # immediately has a usable hunt brain.
    "solo-coach": {
        "description": "Solo executive / business coach hunting their first 50 clients",
        "icp": "Independent executive coach selling 1:1 engagements ($1-5k/month) to mid-career operators ready to move into leadership.",
        "target_clients": "Senior managers and directors at 50–500 person companies who recently got promoted or are about to leave for a founder role. Pain: imposter syndrome, scope expansion, board pressure.",
        "tone": "warm",
        "config": {
            "countries": ["USA", "United Kingdom", "Canada"],
            "queries": [
                "newly promoted director SaaS startup",
                "VP engineering recently joined Series B",
                "head of product first 90 days post-promotion",
                "director engineering startup leaving big tech",
                "operations leader post-IPO transition",
                "newly hired CTO Series A startup",
                "first-time VP looking for executive coach",
                "leadership transition senior manager to director",
            ],
            "max_leads": 20,
        },
    },
    "consultant-fractional": {
        "description": "Fractional CMO / CFO / CTO landing 5 retainer clients per year",
        "icp": "Fractional senior executive selling 3-month engagements ($8-20k/month) to founder-led companies that have outgrown DIY.",
        "target_clients": "Founder-led B2B companies, $1-10M ARR, no full-time exec in your function yet. Pain: founder is overstretched, growth is plateauing, hiring full-time too expensive too early.",
        "tone": "consultative",
        "config": {
            "countries": ["USA", "United Kingdom", "Germany"],
            "queries": [
                "Series A founder hiring fractional CMO",
                "B2B SaaS $2M ARR no marketing leader",
                "founder-led startup outgrown founder marketing",
                "Seed-stage company hiring head of growth",
                "5M ARR boutique consulting need fractional CFO",
                "fast-growing agency needs fractional CTO",
                "Series A founder overstretched marketing operations",
                "post-PMF startup first marketing hire",
            ],
            "max_leads": 20,
        },
    },
    "video-production": {
        "description": "Video / motion / animation studio finding agencies that outsource",
        "icp": "Boutique video production studio (3-15 people) selling project-based engagements ($5-50k) to agencies and brand teams that need overflow capacity.",
        "target_clients": "Mid-size agencies with in-house creative but no dedicated video team. Brand teams at scale-ups with monthly content schedules. Pain: in-house can't keep up, freelancers are inconsistent.",
        "tone": "friendly",
        "config": {
            "countries": ["United Kingdom", "Germany", "France", "Netherlands"],
            "queries": [
                "creative agency Berlin produces video monthly",
                "marketing agency London outsources video production",
                "branding agency Amsterdam needs animation",
                "scale-up brand team monthly video content",
                "B2B SaaS company recently launched product video",
                "podcast network growing hiring producer",
                "DTC brand scaling video content team",
                "agency Paris growing motion design needs",
            ],
            "max_leads": 25,
        },
    },
    "saas-developer-tools": {
        "description": "Devtools SaaS hunting open-source / DX leads at engineering-led companies",
        "icp": "Developer-tools SaaS ($20-200/seat/mo) selling to engineering teams at fast-growing startups. Open-source halo, technical content, no sales-led GTM.",
        "target_clients": "Engineering managers and platform-engineering leaders at 50–500-person startups with active GitHub orgs and technical blogs. Pain: scaling internal infra, on-call burden, dev productivity.",
        "tone": "friendly",
        "config": {
            "countries": ["USA", "United Kingdom", "Germany", "France"],
            "queries": [
                "platform engineering team scaling Series B",
                "SaaS company hiring SRE Series A growth-stage",
                "engineering manager developer experience hiring",
                "Kubernetes platform team scaling 50-200 engineers",
                "infrastructure team Series B startup",
                "engineering blog active open source contributor",
                "DX team hiring platform engineer",
                "post-Series-A startup hiring senior platform engineer",
            ],
            "max_leads": 25,
        },
    },
    "design-studio": {
        "description": "Design studio looking for B2B SaaS clients post-rebrand",
        "icp": "Brand / product-design studio (5-20 people) selling project work ($25-150k) to B2B SaaS companies post-Series-A.",
        "target_clients": "B2B SaaS companies that just raised Series A or B, on a generic 'startup' visual identity, growing fast, design team thin or absent.",
        "tone": "warm",
        "config": {
            "countries": ["USA", "United Kingdom", "Germany"],
            "queries": [
                "Series B SaaS company recent rebrand",
                "B2B SaaS Series A no in-house designer",
                "post-Series-A startup hiring head of design",
                "SaaS company recently raised hiring brand designer",
                "fintech startup Series A hiring product designer",
                "vertical SaaS Series B no design team",
                "B2B startup recent funding outdated brand",
                "Series A SaaS founder looking for design studio",
            ],
            "max_leads": 20,
        },
    },
    "podcast-producer": {
        "description": "Podcast production studio looking for B2B founders ready to launch a show",
        "icp": "Boutique podcast production studio (2-8 people) selling done-for-you show production ($3-10k/month) to thought-leader founders and exec-led B2B brands.",
        "target_clients": "Founder-led B2B companies whose CEO speaks at conferences, posts on LinkedIn, and has obvious thought leadership. Pain: wants a podcast, doesn't have time to produce one.",
        "tone": "friendly",
        "config": {
            "countries": ["USA", "United Kingdom"],
            "queries": [
                "B2B SaaS CEO speaking at conferences 2026",
                "founder-led startup active LinkedIn thought leader",
                "Series B founder published industry whitepaper",
                "CEO recently keynoted SaaS conference",
                "B2B founder large LinkedIn following 50K",
                "scale-up CEO recent podcast guest appearance",
                "founder-led company hiring head of content",
                "thought-leader CEO 100K LinkedIn followers",
            ],
            "max_leads": 20,
        },
    },
}


def cmd_examples(args: argparse.Namespace) -> int:
    """List or install bundled starter recipes.

    Subcommands:
      ls                       List available examples
      install <name>           Save the recipe locally so `huntova recipe run <name>` works

    Examples:
        huntova examples ls
        huntova examples install agencies-eu
        huntova recipe run agencies-eu
    """
    sub = (args.subcommand or "ls").lower()
    color = sys.stdout.isatty()
    bold = (lambda s: f"\033[1m{s}\033[0m") if color else (lambda s: s)
    dim = (lambda s: f"\033[2m{s}\033[0m") if color else (lambda s: s)
    green = (lambda s: f"\033[32m{s}\033[0m") if color else (lambda s: s)

    if sub == "ls":
        print(f"\n{bold(f'{len(_BUNDLED_EXAMPLES)} bundled example recipe(s):')}\n")
        for name, spec in _BUNDLED_EXAMPLES.items():
            cfg = spec["config"]
            countries = ", ".join(cfg.get("countries", []))[:50]
            qcount = len(cfg.get("queries", []))
            print(f"  {bold(name)}")
            print(f"    {dim(spec['description'])}")
            print(f"    {dim(f'{qcount} queries · {countries}')}")
            print(f"    {dim('install:')} huntova examples install {name}\n")
        return 0

    if sub == "install":
        name = (args.name or "").strip().lower()
        if not name:
            print("[huntova] usage: huntova examples install <name>", file=sys.stderr)
            print("           run `huntova examples ls` to see available names.", file=sys.stderr)
            return 1
        if name not in _BUNDLED_EXAMPLES:
            print(f"[huntova] unknown example {name!r}.", file=sys.stderr)
            print(f"           known: {', '.join(_BUNDLED_EXAMPLES.keys())}", file=sys.stderr)
            return 1
        user_id = _bootstrap_local_env()
        if user_id is None:
            return 1
        import asyncio as _asyncio
        import db as _db
        existing = _asyncio.run(_db.get_hunt_recipe(user_id, name))
        if existing and not getattr(args, "force", False):
            print(f"[huntova] recipe {name!r} already exists — re-run with --force to overwrite.", file=sys.stderr)
            return 1
        spec = _BUNDLED_EXAMPLES[name]
        _asyncio.run(_db.save_hunt_recipe(
            user_id, name,
            description=spec["description"],
            config=spec["config"],
        ))
        # If the playbook ships an ICP brief + target_clients, seed
        # the wizard with them so the agent has a usable hunt brain
        # immediately. Existing wizard fields are preserved unless
        # --force is passed (so we don't overwrite the user's own ICP
        # the first time they install a second playbook).
        seeded = False
        if spec.get("icp") or spec.get("target_clients") or spec.get("tone"):
            current = _asyncio.run(_db.get_settings(user_id)) or {}
            wiz = dict(current.get("wizard") or {})
            mutated = False
            if spec.get("icp") and (not wiz.get("business_description") or getattr(args, "force", False)):
                wiz["business_description"] = spec["icp"]
                mutated = True
            if spec.get("target_clients") and (not wiz.get("target_clients") or getattr(args, "force", False)):
                wiz["target_clients"] = spec["target_clients"]
                mutated = True
            if mutated:
                current["wizard"] = wiz
            if spec.get("tone") and (not current.get("default_tone") or getattr(args, "force", False)):
                current["default_tone"] = spec["tone"]
                mutated = True
            if mutated:
                _asyncio.run(_db.save_settings(user_id, current))
                seeded = True
        print(f"\n{green('✓')} installed playbook {bold(name)}\n")
        if seeded:
            print(f"  {dim('seeded:')} ICP + target clients + tone wired into the wizard.")
        print(f"  Run it with:")
        print(f"    {dim('$')} huntova recipe run {name}")
        print(f"  Inspect first:")
        print(f"    {dim('$')} huntova recipe inspect {name}\n")
        metrics_emit("cli_example_install", {"name": name, "seeded": seeded})
        return 0

    print(f"[huntova] unknown examples subcommand {sub!r} — try `ls` or `install <name>`.", file=sys.stderr)
    return 1


def cmd_cloud(args: argparse.Namespace) -> int:
    """Cloud Proxy admin operations.

    Subcommand `token`:
      huntova cloud token mint <email> [--quota 200]
        → mints a per-user Cloud Search token + prints the
          HV_SEARXNG_URL the partner sets to use it.
    """
    sub = (args.subcommand or "").lower()
    if sub == "token":
        action = (args.action_or_email or "").lower().strip()
        if action != "mint":
            print("[huntova] usage: huntova cloud token mint <email> [--quota 200] [--plan design_partner]", file=sys.stderr)
            return 1
        email = (args.email or "").strip()
        if not email or "@" not in email:
            print("[huntova] usage: huntova cloud token mint <email>", file=sys.stderr)
            return 1
        token = os.environ.get("HV_ADMIN_TOKEN", "").strip()
        if not token:
            print("[huntova] set HV_ADMIN_TOKEN to the value matching the server.", file=sys.stderr)
            return 1
        base = os.environ.get("HV_PUBLIC_URL", "https://huntova.com").rstrip("/")
        endpoint = f"{base}/api/admin/cloud-token"
        try:
            import urllib.request
            import json as _json
            body_bytes = _json.dumps({
                "email": email,
                "plan": args.plan or "design_partner",
                "daily_quota": int(args.quota or 200),
                "notes": args.notes or "",
            }).encode("utf-8")
            req = urllib.request.Request(
                endpoint, data=body_bytes, method="POST",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "User-Agent": f"huntova-cli/{VERSION}",
                },
            )
            with urllib.request.urlopen(req, timeout=15) as r:
                resp = _json.loads(r.read().decode("utf-8", errors="ignore") or "{}")
        except urllib.error.HTTPError as e:
            try:
                err = _json.loads(e.read().decode("utf-8", errors="ignore") or "{}")
                msg = err.get("message") or err.get("error") or str(e.code)
            except Exception:
                msg = str(e.code)
            print(f"[huntova] {endpoint} returned {e.code}: {msg}", file=sys.stderr)
            return 1
        except Exception as e:
            print(f"[huntova] couldn't reach {endpoint}: {type(e).__name__}: {e}", file=sys.stderr)
            return 1
        if not resp.get("ok"):
            print(f"[huntova] mint failed: {resp.get('message') or resp.get('error')}", file=sys.stderr)
            return 1
        color = sys.stdout.isatty()
        bold = (lambda s: f"\033[1m{s}\033[0m") if color else (lambda s: s)
        green = (lambda s: f"\033[32m{s}\033[0m") if color else (lambda s: s)
        print(f"\n{bold('cloud-proxy token minted:')}\n")
        print(f"  email:        {email}")
        print(f"  plan:         {resp.get('plan')}")
        print(f"  daily quota:  {resp.get('daily_quota')}")
        print(f"  endpoint:     {green(resp.get('endpoint',''))}")
        print(f"\n{bold('Send this to the design partner:')}\n")
        print(f"  export HV_SEARXNG_URL={resp.get('endpoint','')}\n")
        print(f"  Then `huntova hunt ...` works unchanged.\n")
        return 0
    print(f"[huntova] unknown cloud subcommand {sub!r} — try `token mint <email>`.", file=sys.stderr)
    return 1


def cmd_metrics(args: argparse.Namespace) -> int:
    """Query the server's /api/admin/metrics for daily event counts.

    Requires HV_ADMIN_TOKEN set in the env to match the server's value.
    Hits HV_PUBLIC_URL by default (https://huntova.com).

    Examples:
        huntova metrics show               # last 7 days, all events
        huntova metrics show --days 14
        huntova metrics show --event try_submit
    """
    sub = (args.subcommand or "show").lower()
    if sub != "show":
        print(f"[huntova] unknown metrics subcommand {sub!r} — try `show`.", file=sys.stderr)
        return 1
    token = os.environ.get("HV_ADMIN_TOKEN", "").strip()
    if not token:
        print("[huntova] set HV_ADMIN_TOKEN in your env (must match server's value).", file=sys.stderr)
        return 1
    base = os.environ.get("HV_PUBLIC_URL", "https://huntova.com").rstrip("/")
    days = max(1, min(int(args.days or 7), 90))
    qs = f"?days={days}"
    if args.event:
        qs += f"&event={args.event}"
    url = f"{base}/api/admin/metrics{qs}"
    try:
        import urllib.request
        import json as _json
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": f"huntova-cli/{VERSION}",
        })
        with urllib.request.urlopen(req, timeout=10) as r:
            payload = _json.loads(r.read().decode("utf-8", errors="ignore") or "{}")
    except urllib.error.HTTPError as e:
        try:
            err = _json.loads(e.read().decode("utf-8", errors="ignore") or "{}")
            msg = err.get("message") or err.get("error") or str(e.code)
        except Exception:
            msg = str(e.code)
        print(f"[huntova] {url} returned {e.code}: {msg}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"[huntova] couldn't reach {url}: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    if not payload.get("ok"):
        print(f"[huntova] server rejected: {payload.get('message') or payload.get('error')}", file=sys.stderr)
        return 1
    rows = payload.get("rows") or []
    if args.format == "json":
        import json as _json
        print(_json.dumps(payload, indent=2, default=str))
        return 0
    color = sys.stdout.isatty()
    bold = (lambda s: f"\033[1m{s}\033[0m") if color else (lambda s: s)
    dim = (lambda s: f"\033[2m{s}\033[0m") if color else (lambda s: s)
    print(f"\n{bold(f'huntova metrics — last {days} day(s)')}\n")
    if not rows:
        print(dim("  (no events recorded yet — needs HV_ADMIN_TOKEN match + telemetry table populated)"))
        return 0
    # Group rows by day for easier reading
    by_day: dict = {}
    totals: dict = {}
    for r in rows:
        d = r.get("day") or "?"
        ev = r.get("event") or "?"
        n = int(r.get("n") or 0)
        by_day.setdefault(d, {})[ev] = n
        totals[ev] = totals.get(ev, 0) + n
    for d in sorted(by_day.keys(), reverse=True):
        print(f"  {bold(d)}")
        for ev, n in sorted(by_day[d].items(), key=lambda kv: kv[1], reverse=True):
            print(f"    {ev:24} {n:>6}")
    print(f"\n  {bold('totals:')}")
    for ev, n in sorted(totals.items(), key=lambda kv: kv[1], reverse=True):
        print(f"    {ev:24} {n:>6}")
    # Quick funnel display when standard launch events are present
    tries = totals.get("try_submit", 0)
    inits = totals.get("cli_init", 0)
    hunts = totals.get("cli_hunt", 0)
    outreach = totals.get("cli_outreach", 0)
    if tries or inits or hunts:
        print(f"\n  {bold('funnel:')}")
        if tries:
            pct = 100.0 * inits / tries if tries else 0
            print(f"    /try → install        {tries:>6} → {inits:>6}  ({pct:.1f}%)")
        if inits:
            pct = 100.0 * hunts / inits if inits else 0
            print(f"    install → first hunt  {inits:>6} → {hunts:>6}  ({pct:.1f}%)")
        if hunts and outreach:
            pct = 100.0 * outreach / hunts if hunts else 0
            print(f"    hunt → outreach send  {hunts:>6} → {outreach:>6}  ({pct:.1f}%)")
    return 0


def cmd_outreach(args: argparse.Namespace) -> int:
    """Send personalised cold emails to recent leads.

    Closes the find → score → SAVE → SEND loop. Reads leads from local
    SQLite, picks the top-N by fit_score, renders each lead's per-row
    AI-drafted email_subject + email_body (or a custom --template if
    supplied), and sends via the user's SMTP. --dry-run prints the
    rendered emails without sending. --max caps the daily blast.

    Examples:
        # send the top 5 leads' AI-drafted emails (the agent already
        # wrote a personalised email per qualified lead during Pass 3)
        huntova outreach send --top 5 --dry-run

        # send up to 10, actually deliver
        huntova outreach send --top 10 --max 10

        # use a custom template instead of the AI's per-row drafts
        huntova outreach send --template ./pitch.txt --top 25
    """
    user_id = _bootstrap_local_env()
    if user_id is None:
        return 1
    import asyncio as _asyncio
    import db as _db

    # Bridge dashboard-saved SMTP settings into env. The Settings →
    # Outreach tab writes smtp_host/smtp_user/smtp_port to user_settings
    # (DB) and smtp_password to the OS keychain via secrets_store. The
    # email_service module reads SMTP_HOST/SMTP_USER/SMTP_PASSWORD from
    # env at import time — without this hydration, dashboard-configured
    # SMTP would be silently ignored by `huntova outreach`.
    try:
        _stg = _asyncio.run(_db.get_settings(user_id)) or {}
        if _stg.get("smtp_host") and not os.environ.get("SMTP_HOST"):
            os.environ["SMTP_HOST"] = str(_stg["smtp_host"])
        if _stg.get("smtp_user") and not os.environ.get("SMTP_USER"):
            os.environ["SMTP_USER"] = str(_stg["smtp_user"])
        if _stg.get("smtp_port") and not os.environ.get("SMTP_PORT"):
            os.environ["SMTP_PORT"] = str(_stg["smtp_port"])
        # Stability fix (audit wave 29): the previous version only
        # hydrated host/user/port/password, leaving SMTP_FROM_EMAIL
        # and SMTP_FROM_NAME unset. email_service._smtp_settings()
        # then defaulted from_email to "noreply@huntova.com" — so
        # every outreach email authenticated as the user but spoofed
        # `From: Huntova <noreply@huntova.com>`. Gmail / Outlook
        # rejected most of these as forgery (550-5.7.1) and any that
        # delivered routed replies to a domain the user didn't own,
        # breaking `huntova inbox watch` reply-matching entirely.
        # Default from_email to the authenticated SMTP user when the
        # user hasn't pinned one explicitly. from_name falls through
        # to settings.from_name → wizard company_name → empty.
        if _stg.get("smtp_user") and not os.environ.get("SMTP_FROM_EMAIL"):
            _from = (_stg.get("from_email") or _stg.get("smtp_user") or "").strip()
            if _from:
                os.environ["SMTP_FROM_EMAIL"] = _from
        if _stg.get("from_name") and not os.environ.get("SMTP_FROM_NAME"):
            os.environ["SMTP_FROM_NAME"] = str(_stg["from_name"])
        if not os.environ.get("SMTP_PASSWORD"):
            try:
                from secrets_store import get_secret
                pw = get_secret("HV_SMTP_PASSWORD")
                if pw:
                    os.environ["SMTP_PASSWORD"] = pw
            except Exception:
                pass
    except Exception:
        pass

    # SMTP must be configured for real sends. --dry-run skips the check.
    smtp_ready = all(os.environ.get(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"))
    if not smtp_ready and not args.dry_run:
        print("[huntova] SMTP not configured. Set it in Settings → Outreach (dashboard)", file=sys.stderr)
        print("           or via SMTP_HOST / SMTP_USER / SMTP_PASSWORD env vars.", file=sys.stderr)
        print("           Or use --dry-run to preview without sending.", file=sys.stderr)
        return 1

    # Pull leads (ordered newest-first; we re-sort by fit below)
    leads = _asyncio.run(_db.get_leads(user_id, limit=200))
    if not leads:
        print("[huntova] no leads found locally — run `huntova hunt` first.", file=sys.stderr)
        return 1

    # Optional custom template — single file with a `Subject:` header line
    # then body. {placeholders} get rendered per-lead.
    custom_subject = ""
    custom_body = ""
    if args.template:
        try:
            raw = open(args.template, "r", encoding="utf-8").read()
        except OSError as e:
            print(f"[huntova] couldn't read template {args.template!r}: {e}", file=sys.stderr)
            return 1
        # First non-empty line beginning with "Subject:" is the subject;
        # rest is the body. Falls back to the lead's AI-drafted subject
        # if the template doesn't supply one.
        lines = raw.splitlines()
        body_start = 0
        for i, ln in enumerate(lines):
            stripped = ln.strip()
            if stripped.lower().startswith("subject:"):
                custom_subject = stripped.split(":", 1)[1].strip()
                body_start = i + 1
                break
        custom_body = "\n".join(lines[body_start:]).strip()

    # Filter + sort. Skip leads without contact_email, skip duplicates,
    # and skip anything already emailed *today* — running
    # `huntova outreach send --top 5` twice in the same day must not
    # double-mail the same prospects (real-world: it just looks like
    # spam to the recipient).
    from datetime import date as _date
    _today_iso = _date.today().isoformat()
    candidates = []
    seen_emails = set()
    skipped_already_sent = 0
    for ld in leads:
        email = (ld.get("contact_email") or "").strip().lower()
        if not email or email in seen_emails:
            continue
        # Already sent today? `_sent_at` and `email_sent_at` are both
        # used in different code paths — check both.
        _sent = (ld.get("_sent_at") or ld.get("email_sent_at") or "")
        if isinstance(_sent, str) and _sent.startswith(_today_iso):
            skipped_already_sent += 1
            continue
        # Need either an AI-drafted email OR a custom template
        has_ai_draft = (ld.get("email_subject") or "").strip() and (ld.get("email_body") or "").strip()
        if not (has_ai_draft or custom_body):
            continue
        seen_emails.add(email)
        try:
            ld["_fit"] = float(ld.get("fit_score") or 0)
        except (TypeError, ValueError):
            ld["_fit"] = 0.0
        candidates.append(ld)
    if skipped_already_sent:
        print(f"[huntova] skipping {skipped_already_sent} lead(s) already emailed today.",
              file=sys.stderr)
    candidates.sort(key=lambda l: l["_fit"], reverse=True)

    top_n = max(1, min(int(args.top or 5), len(candidates)))
    daily_cap = max(1, int(args.max_send or top_n))
    cap = min(top_n, daily_cap)
    targets = candidates[:cap]

    if not targets:
        print("[huntova] no eligible leads (need contact_email + email draft or --template).", file=sys.stderr)
        return 1

    color = sys.stdout.isatty()
    bold = (lambda s: f"\033[1m{s}\033[0m") if color else (lambda s: s)
    dim = (lambda s: f"\033[2m{s}\033[0m") if color else (lambda s: s)
    green = (lambda s: f"\033[32m{s}\033[0m") if color else (lambda s: s)
    red = (lambda s: f"\033[31m{s}\033[0m") if color else (lambda s: s)

    def _render(template: str, lead: dict) -> str:
        # Safe placeholder substitution — unknown placeholders left as-is
        # (printed visibly) so a missing field is obvious in dry-run.
        ctx = {
            "org_name": lead.get("org_name") or "",
            "contact_name": (lead.get("contact_name") or "").split()[0] if lead.get("contact_name") else "there",
            "contact_role": lead.get("contact_role") or "",
            "evidence_quote": (lead.get("evidence_quote") or "")[:200],
            "event_name": lead.get("event_name") or "",
            "why_fit": lead.get("why_fit") or "",
            "country": lead.get("country") or "",
            "city": lead.get("city") or "",
            "site": lead.get("org_website") or "",
        }
        try:
            return template.format(**{k: v for k, v in ctx.items()})
        except (KeyError, IndexError, ValueError):
            # Add ValueError so a template containing literal `{` / `}`
            # (e.g. user wrote a JSON-style snippet or a bare `{` in
            # subject) doesn't crash the whole outreach loop. Fall back
            # to the raw template — caller still gets something to send.
            return template

    print(f"\n{bold(f'huntova outreach — {len(targets)} email(s) to send' + (' (dry-run)' if args.dry_run else ''))}\n")
    # Optional: deep-research any lead whose fit_score crosses the
    # threshold *before* sending. Costs one extra AI call per
    # researched lead, but rewrites the opener using a 14-page crawl
    # so high-fit prospects get hyper-personalised emails. Skips
    # silently when --research-above is 0 (default) or when the lead
    # has no org_website to crawl.
    _research_thr = float(getattr(args, "research_above", 0.0) or 0.0)
    if _research_thr > 0 and not args.dry_run:
        from argparse import Namespace as _NS
        _to_research = [l for l in targets
                        if float(l.get("fit_score") or 0) >= _research_thr
                        and (l.get("org_website") or "").strip()
                        and l.get("lead_id")]
        if _to_research:
            print(f"  {dim(f'researching {len(_to_research)} lead(s) above fit={_research_thr} before send...')}\n")
            for ld in _to_research:
                _ns = _NS(lead_id=ld["lead_id"], pages=14, tone="")
                try:
                    cmd_research(_ns)
                except SystemExit:
                    pass
                except Exception as _re:
                    print(f"  {yellow('!')} research {ld.get('lead_id')} failed: {type(_re).__name__}: {str(_re)[:80]}")
            # Reload lead rows from DB so the rewritten subject/body
            # is what the loop below picks up.
            try:
                _refreshed = _asyncio.run(_db.get_leads(user_id, limit=200))
                _by_id = {ld.get("lead_id"): ld for ld in (_refreshed or [])}
                targets = [_by_id.get(t.get("lead_id"), t) for t in targets]
            except Exception:
                pass
            print()

    sent = 0
    failed = 0
    for i, lead in enumerate(targets, start=1):
        to = (lead.get("contact_email") or "").strip()
        if custom_body:
            subject = _render(custom_subject or lead.get("email_subject") or "Quick note", lead)
            body = _render(custom_body, lead)
        else:
            subject = (lead.get("email_subject") or "").strip()
            body = (lead.get("email_body") or "").strip()
        org = (lead.get("org_name") or "?")[:40]
        fit = lead.get("fit_score", "?")
        print(f"{dim(f'#{i}')} {bold(org)} → {to}  {dim(f'fit={fit}')}")
        print(f"  {dim('subject:')} {subject[:80]}")
        print(f"  {dim('body:   ')} {body[:140].replace(chr(10), ' / ')}{'...' if len(body) > 140 else ''}")
        if args.dry_run:
            print(f"  {dim('(dry-run — not sent)')}")
            print()
            continue
        try:
            from email_service import _send_email_sync
            # Plain-text first; HTML body is the same wrapped in <pre>
            # so the email is readable in both views without a template
            # layer that adds Huntova branding (the user's outreach
            # should look like the user, not like Huntova).
            html = "<pre style='font-family:inherit;white-space:pre-wrap;font-size:14px'>" + body.replace("<", "&lt;").replace(">", "&gt;") + "</pre>"
            _msg_id = _send_email_sync(to, subject, html, plain_body=body)
            sent += 1
            # Persist Message-ID + sent-at on the lead row so
            # `huntova inbox watch` can match incoming replies via
            # `In-Reply-To` / `References` headers.
            _now_iso = __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc).isoformat()
            _lid = lead.get("lead_id") or ""
            if _lid and _msg_id:
                def _stamp(_l, _mid=_msg_id, _ts=_now_iso, _to=to):
                    _l["_message_id"] = (_mid or "").strip("<>")
                    _l["_sent_at"] = _ts
                    _l["_sent_to"] = _to
                    # Enrol at step 1 so `huntova sequence run` picks
                    # this lead up for the Day +4 bump and Day +9
                    # final. Reply detection (`huntova inbox watch`)
                    # flips _seq_paused=True before then if the
                    # recipient writes back.
                    if not int(_l.get("_seq_step") or 0):
                        _l["_seq_step"] = 1
                        _l["_seq_paused"] = False
                        _l["_seq_last_at"] = _ts
                    return _l
                try:
                    _asyncio.run(_db.merge_lead(user_id, _lid, _stamp))
                except Exception:
                    pass
            try:
                _asyncio.run(_db.save_lead_action(
                    user_id, _lid or "?", "email_sent",
                    score_band=str(int(lead.get("_fit", 0))),
                    meta=__import__("json").dumps({"to": to, "subject": subject[:80]}),
                ))
            except Exception:
                pass
            print(f"  {green('✓ sent')}")
        except Exception as e:
            failed += 1
            print(f"  {red(f'✗ failed: {type(e).__name__}: {str(e)[:80]}')}")
        print()

    print(f"\n{bold('summary:')}")
    print(f"  · sent:    {green(str(sent))}")
    if failed:
        print(f"  · failed:  {red(str(failed))}")
    if args.dry_run:
        print(f"  · {dim('(dry-run mode — re-run without --dry-run to deliver)')}")
    metrics_emit("cli_outreach", {"sent": sent, "failed": failed,
                                    "dry_run": bool(args.dry_run),
                                    "template": bool(args.template)})
    return 0 if failed == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="huntova",
        description="Huntova — local-first lead-gen super-tool.",
    )
    # `huntova --version` is the first thing most users type. Ship the
    # standard argparse `--version` action (alongside the existing
    # `huntova version` subcommand for backwards compat).
    p.add_argument("--version", action="version", version=f"huntova {VERSION}")
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("serve", help="Run the local web app on 127.0.0.1 (closing the terminal stops the server)")
    s.add_argument("--no-update-check", action="store_true",
                   help="skip the GitHub-Releases launch-time update check")
    s.add_argument("--host", default=None, help=f"bind host (default {DEFAULT_HOST})")
    s.add_argument("--port", default=None, type=int, help=f"bind port (default {DEFAULT_PORT})")
    s.add_argument("--no-browser", action="store_true", help="don't auto-open the browser")
    s.add_argument("--logs", action="store_true",
                   help="(a284) live colored event stream in this terminal — see hunts, leads, "
                        "sequences, inbox events as they happen")
    s.set_defaults(func=cmd_serve)

    # a284: terminal-first surfaces — tail + run. Closing the terminal
    # ends the local server anyway, so make the terminal useful while
    # it's open: live event stream + interactive console.
    tl = sub.add_parser("tail",
                         help="(a284) live event stream from a running local server (Ctrl+C to stop)")
    tl.add_argument("--host", default=None, help=f"server host (default {DEFAULT_HOST})")
    tl.add_argument("--port", default=None, type=int, help=f"server port (default {DEFAULT_PORT})")
    def _dispatch_tail(args):
        from cli_terminal import cmd_tail
        return cmd_tail(args)
    tl.set_defaults(func=_dispatch_tail)

    rn = sub.add_parser("run",
                         help="(a284) start the server + open an interactive console in this terminal")
    rn.add_argument("--host", default=None, help=f"bind host (default {DEFAULT_HOST})")
    rn.add_argument("--port", default=None, type=int, help=f"bind port (default {DEFAULT_PORT})")
    def _dispatch_run(args):
        from cli_terminal import cmd_run
        return cmd_run(args)
    rn.set_defaults(func=_dispatch_run)

    i = sub.add_parser("init", help="Initialise config dir + db dir (zero-interactive by default)")
    i.add_argument("--force", action="store_true", help="overwrite existing config")
    i.add_argument("--wizard", action="store_true",
                   help="Friendly interactive setup that prompts for provider + API key")
    i.set_defaults(func=cmd_init)

    st = sub.add_parser("status",
                        help="Operational dashboard — daemon, server, providers, plugins, data, last hunt")
    st.add_argument("--format", choices=("text", "json"), default="text",
                     help="Output format (default: text)")
    st.set_defaults(func=cmd_status)

    cf = sub.add_parser(
        "config",
        help="Show / edit / validate the config (path / show / get / set / unset / validate / edit)",
        epilog="Docs: https://github.com/enzostrano/huntova-public/blob/main/docs/CONFIG.md",
    )
    cf.add_argument("subcommand", nargs="?", default="show",
                     choices=("show", "edit", "get", "set", "unset", "validate", "path"),
                     help="Action (default: show). validate=sanity-check the config; unset=delete a key.")
    cf.add_argument("key", nargs="?", default="",
                     help="Config key (for get / set / unset)")
    cf.add_argument("value", nargs="?", default="",
                     help="Value to set (for set)")
    cf.set_defaults(func=cmd_config)

    ti = sub.add_parser("test-integrations",
                        help="Probe every configured integration (AI / SearXNG / Playwright / plugins / SMTP)")
    ti.add_argument("--format", choices=("text", "json"), default="text")
    ti.set_defaults(func=cmd_test_integrations)

    dn = sub.add_parser("daemon",
                        help="Install / control the Huntova background daemon (launchd/systemd)")
    dn.add_argument("subcommand", nargs="?", default="status",
                    choices=("install", "uninstall", "start", "stop", "status", "logs"),
                    help="Action: install / uninstall / start / stop / status / logs")
    dn.add_argument("--port", type=int, default=0,
                    help=f"Bind port for daemon (default {DEFAULT_PORT})")
    dn.add_argument("-f", "--follow", action="store_true",
                    help="Follow log output (logs subcommand only)")
    dn.set_defaults(func=cmd_daemon)

    ob = sub.add_parser(
        "onboard",
        help="First-run wizard — picks provider, saves key, opens dashboard (RECOMMENDED for new users)",
        description=(
            "Three modes:\n"
            "  default                 Interactive TUI wizard (recommended).\n"
            "  --browser               Skip TUI; open web wizard at /setup.\n"
            "  --no-prompt             Non-interactive (CI / scripted).\n"
            "\n"
            "Reset on top of any mode with --reset-scope to start fresh.\n"
        ),
        epilog="Docs: https://github.com/enzostrano/huntova-public/blob/main/docs/ONBOARDING.md",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ob.add_argument("--browser", action="store_true",
                    help="Skip the TUI and open the web wizard at /setup")
    ob.add_argument("--no-launch", action="store_true",
                    help="Don't auto-start `huntova serve` after setup completes")
    ob.add_argument("--no-prompt", action="store_true",
                    help="Non-interactive mode (CI / scripted) — fails if key not in env")
    ob.add_argument("--force", action="store_true",
                    help="Re-prompt even if a key is already saved")
    ob.add_argument(
        "--reset-scope",
        choices=("config", "keys", "full"),
        default=None,
        help=(
            "Wipe state before running the wizard. "
            "config=erase config.toml only · keys=erase config + keychain entries · "
            "full=erase config + keychain + local DB."
        ),
    )
    # Flow / safety / output flags. --flow picks the
    # ceremony level. --accept-risk acknowledges that an autonomous AI
    # agent has full network access on your machine — required pairing
    # with --no-prompt for non-interactive scripted setup.
    # --json emits a structured summary so scripted setups can parse
    # the result instead of scraping stderr.
    ob.add_argument("--flow", choices=("quickstart", "advanced", "manual"),
                    default="quickstart",
                    help="quickstart=2 questions (provider+key) · advanced=DB/data-dir prompts too · manual=every option (default: quickstart)")
    ob.add_argument("--accept-risk", action="store_true",
                    help="Acknowledge the agent has full network access (required for --no-prompt)")
    ob.add_argument("--json", action="store_true",
                    help="Emit a structured JSON summary on completion (for scripted runs)")
    ob.add_argument("--mode", choices=("local", "remote"),
                    default="local",
                    help="local=onboard against this machine (default) · remote=onboard against an existing huntova serve URL")
    # Per-provider keys for scripted setup (one --*-api-key flag per
    # --gemini-api-key / --anthropic-api-key / etc.). Scripts pass any
    # of these and the wizard skips the interactive paste step.
    for slug, env_var in (
        ("gemini",     "HV_GEMINI_KEY"),
        ("anthropic",  "HV_ANTHROPIC_KEY"),
        ("openai",     "HV_OPENAI_KEY"),
        ("openrouter", "HV_OPENROUTER_KEY"),
        ("groq",       "HV_GROQ_KEY"),
        ("deepseek",   "HV_DEEPSEEK_KEY"),
        ("together",   "HV_TOGETHER_KEY"),
        ("mistral",    "HV_MISTRAL_KEY"),
        ("perplexity", "HV_PERPLEXITY_KEY"),
        ("ollama",     "HV_OLLAMA_KEY"),
        ("lmstudio",   "HV_LMSTUDIO_KEY"),
        ("llamafile",  "HV_LLAMAFILE_KEY"),
    ):
        ob.add_argument(f"--{slug}-api-key", default=None, dest=f"key_{slug}",
                        help=f"{slug.capitalize()} API key (saved to keychain). Equivalent to setting {env_var} in env.")
    ob.add_argument("--custom-base-url", default=None, dest="custom_base_url",
                    help="Custom OpenAI-compatible endpoint URL (use with --custom-api-key)")
    ob.add_argument("--custom-api-key", default=None, dest="key_custom",
                    help="Custom-endpoint API key (saved to keychain)")
    ob.add_argument("--custom-model", default=None, dest="custom_model",
                    help="Custom-endpoint model ID (e.g. 'qwen/qwen2.5-72b-instruct')")
    ob.set_defaults(func=cmd_onboard)

    d = sub.add_parser(
        "doctor", help="Diagnostic dump",
        epilog="Docs: https://github.com/enzostrano/huntova-public/blob/main/docs/TROUBLESHOOTING.md",
    )
    d.add_argument("--quick", action="store_true",
                   help="Skip live network probes (AI ping + SearXNG round-trip). For CI / smoke runs.")
    d.add_argument("--email", action="store_true",
                   help="Run the deliverability pre-flight (SPF/DKIM/DMARC for sender + MX for recent recipient domains). Requires `pip install dnspython`.")
    d.add_argument("--verbose", action="store_true",
                   help="Used with --email to dump every recipient domain instead of the first 12.")
    d.set_defaults(func=cmd_doctor)

    sec = sub.add_parser(
        "security",
        help="Local security audit (file modes, plaintext fallbacks, env leaks)",
    )
    sec_sub = sec.add_subparsers(dest="security_cmd")
    sec_audit = sec_sub.add_parser("audit", help="Run the audit and print a report")
    sec_audit.add_argument("--json", action="store_true",
                           help="Emit findings as JSON (for scripted runs / CI gates)")
    sec_audit.set_defaults(func=cmd_security)
    # bare `huntova security` defaults to audit. Inject json=False so cmd_security
    # can read args.json unconditionally.
    sec.set_defaults(func=cmd_security, json=False)

    v = sub.add_parser("version", help="Print version")
    v.set_defaults(func=cmd_version)

    u = sub.add_parser("update", help="Upgrade Huntova to the latest version (or --check to query GitHub)")
    u.add_argument("--check", action="store_true",
                   help="don't upgrade — just print whether a newer release exists")
    u.set_defaults(func=cmd_update)

    ch = sub.add_parser(
        "chat",
        help="Natural-language REPL — say what you want, AI dispatches the right command",
        epilog="Docs: https://github.com/enzostrano/huntova-public/blob/main/docs/CHAT.md",
    )
    ch.set_defaults(func=cmd_chat)

    h = sub.add_parser(
        "hunt", help="One-shot headless hunt (no browser needed)",
        epilog="Docs: https://github.com/enzostrano/huntova-public/blob/main/docs/CONFIG.md",
    )
    h.add_argument("--countries", default="",
                   help="Comma-separated list (default: UK,USA,DE,FR,ES,IT)")
    h.add_argument("--max-leads", type=int, default=0,
                   help="Stop after N leads found (0 = until agent finishes)")
    h.add_argument("--verbose", action="store_true",
                   help="Show every log + thought event")
    h.add_argument("--json", action="store_true",
                   help="Emit one JSON object per line (status, lead, log, summary). Errors → stderr.")
    h.add_argument("--dry-run", action="store_true",
                   help="Walk setup (DB, user, countries, providers) but don't start the agent")
    h.add_argument("--from-share", default="",
                   help="Fork a public share — give a /h/<slug> URL or bare slug")
    h.add_argument("--explain-scores", action="store_true",
                   help="Print per-lead score breakdown (fit/buy/timing/reach) + adaptation trace")
    h.set_defaults(func=cmd_hunt)

    ls = sub.add_parser("ls", help="List saved leads in the local SQLite DB")
    ls.add_argument("--limit", type=int, default=20, help="Number of leads to show (default 20)")
    ls.add_argument("--format", choices=("table", "json"), default="table",
                    help="Output format (default: table)")
    ls.add_argument("--filter", default="",
                    help='Substring filter: "aurora" or field-prefixed "country:Germany"')
    # Friendlier shortcut over `--filter status:<s>`. Mirrors the
    # CRM column users actually filter on.
    ls.add_argument("--status", default="",
                    choices=("", "new", "email_sent", "followed_up",
                             "replied", "meeting_booked", "won", "lost",
                             "ignored"),
                    help="Show only leads with this email_status (shortcut for --filter status:<s>)")
    # Quick filter on the IMAP-classifier reply class (a120).
    ls.add_argument("--reply-class", default="",
                    choices=("", "interested", "not_now", "not_interested",
                             "wrong_person", "unsubscribe", "out_of_office"),
                    help="Show only leads whose last reply was classified this way")
    # Quick fit-score gate.
    ls.add_argument("--min-fit", type=int, default=0,
                    help="Show only leads with fit_score >= MIN_FIT")
    ls.set_defaults(func=cmd_ls)

    ex = sub.add_parser("export", help="Export saved leads (CSV or JSON) to stdout")
    ex.add_argument("--format", choices=("csv", "json"), default="csv",
                    help="Output format (default: csv)")
    ex.set_defaults(func=cmd_export)

    sh = sub.add_parser("share", help="Mint or query share links (status <slug> for view count)")
    sh.add_argument("subcommand", nargs="?", default="mint",
                    help='"mint" (default) creates a new share, "status <slug>" shows views. '
                         'A bare slug is treated as "status <slug>".')
    sh.add_argument("slug", nargs="?", default="",
                    help="Slug or /h/<slug> URL (for `status`)")
    sh.add_argument("--top", type=int, default=10, help="How many top-fit leads to include (default 10) — mint only")
    sh.add_argument("--title", default="", help="Optional title for the public page — mint only")
    sh.set_defaults(func=cmd_share)

    rm = sub.add_parser("rm", help="Delete a lead permanently from local DB")
    rm.add_argument("lead_id", help="The lead_id to delete (e.g. L3)")
    rm.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    rm.set_defaults(func=cmd_rm)

    rc = sub.add_parser("recipe", help="Save / list / run / inspect / adapt / publish / import-url / export / import / diff")
    rc.add_argument("subcommand",
                    choices=("ls", "save", "run", "rm", "inspect", "adapt",
                             "publish", "import-url", "export", "import", "diff"),
                    help="Action: ls / save / run / rm / inspect / adapt <name> / publish <name> / import-url <url> / export [--name N --out PATH] / import <path> / diff <local> <path>")
    rc.add_argument("name", nargs="?", default="",
                    help="Recipe name (save/run/rm/publish/export) or URL/path (import-url/import/diff)")
    rc.add_argument("name2", nargs="?", default="",
                    help="Second positional: imported-path for `diff`")
    rc.add_argument("--countries", default="", help="Comma-separated country list (save)")
    rc.add_argument("--max-leads", type=int, default=0, help="Stop after N leads (save)")
    rc.add_argument("--queries", default="", help="Pipe-separated pre-set queries (save)")
    rc.add_argument("--description", default="", help="Optional human-readable note (save)")
    rc.add_argument("--dry-run", action="store_true", help="Walk run setup without firing the agent")
    rc.add_argument("--force", action="store_true", help="adapt: lower the outcomes threshold from 20 to 5; import: overwrite an existing recipe of same name")
    rc.add_argument("--format", choices=("table", "json"), default="table", help="Output format (ls/inspect)")
    rc.add_argument("--out", default="", help="Output path for `recipe export` (default: ~/huntova-<name>-<date>.toml)")
    rc.add_argument("--name", dest="export_name", default="", help="Recipe name for `export` (alternative to positional)")
    rc.set_defaults(func=cmd_recipe)

    pl = sub.add_parser("plugins", help="List/install/create/search plugins (default: list)")
    pl.add_argument("subcommand", nargs="?", default="list",
                    choices=("list", "ls", "create", "search", "install", "contribute"),
                    help="`list` (alias `ls`, default) / `create <name>` / `search [query]` / `install <name>` / `contribute`")
    pl.add_argument("create_name", nargs="?", default="",
                    help="Name for `create` (kebab-case) or query for `search`")
    pl.add_argument("--format", choices=("table", "json"), default="table",
                    help="Output format for `list` (default: table)")
    pl.add_argument("--force", action="store_true",
                    help="Overwrite an existing plugin file when using `create`")
    pl.set_defaults(func=cmd_plugins)

    cp = sub.add_parser("completion", help="Print shell completion code (bash/zsh/fish)")
    cp.add_argument("shell", nargs="?", default="bash", choices=("bash", "zsh", "fish"),
                    help="Target shell (default: bash)")
    cp.set_defaults(func=cmd_completion)

    ic = sub.add_parser("install-completion",
                        help="Auto-install shell completion (writes static files, no eval)")
    ic.add_argument("--shell", choices=("bash", "zsh", "fish"), default=None,
                    help="Target shell (default: auto-detect from $SHELL)")
    ic.add_argument("--uninstall", action="store_true",
                    help="Remove the installed completion file and rc snippet")
    ic.add_argument("--dry-run", action="store_true",
                    help="Show what would be written without touching the filesystem")
    ic.set_defaults(func=cmd_install_completion)

    tm = sub.add_parser("telemetry", help="Opt-in anonymous usage telemetry (enable/disable/status)")
    tm.add_argument("action", nargs="?", default="status",
                    choices=("enable", "disable", "status"),
                    help="enable / disable / status (default: status)")
    tm.set_defaults(func=cmd_telemetry)

    st = sub.add_parser("settings", help="Toggle local CLI settings — currently: auto_update_on_launch")
    st.add_argument("key", nargs="?", default="",
                    help="setting key (e.g. auto_update or auto_update_on_launch)")
    st.add_argument("value", nargs="?", default="",
                    help="on / off / show")
    st.set_defaults(func=cmd_settings)

    ex_p = sub.add_parser(
        "examples",
        aliases=["playbook", "playbooks"],
        help="Bundled playbooks — `ls` or `install <name>`. Auto-seeds wizard ICP / target_clients / tone.",
    )
    ex_p.add_argument("subcommand", nargs="?", default="ls",
                     choices=("ls", "install"),
                     help="`ls` (default) or `install <name>`")
    ex_p.add_argument("name", nargs="?", default="",
                     help="Playbook name (for `install`)")
    ex_p.add_argument("--force", action="store_true",
                     help="Overwrite an existing same-named recipe")
    ex_p.set_defaults(func=cmd_examples)

    cl = sub.add_parser("cloud", help="Cloud Proxy admin (mint design-partner tokens)")
    cl.add_argument("subcommand", nargs="?", default="", choices=("token",),
                    help="Cloud subcommand")
    cl.add_argument("action_or_email", nargs="?", default="",
                    help="For `token`, this is `mint`. Pass the email after.")
    cl.add_argument("email", nargs="?", default="",
                    help="Design partner email (for `token mint`)")
    cl.add_argument("--quota", type=int, default=200,
                    help="Daily search quota (default 200)")
    cl.add_argument("--plan", default="design_partner",
                    help="Plan label (default 'design_partner')")
    cl.add_argument("--notes", default="",
                    help="Free-text note about who/why")
    cl.set_defaults(func=cmd_cloud)

    me = sub.add_parser("metrics", help="Query daily metrics counts (admin: needs HV_ADMIN_TOKEN)")
    me.add_argument("subcommand", nargs="?", default="show",
                    choices=("show",),
                    help="Action (currently only 'show')")
    me.add_argument("--days", type=int, default=7, help="Look back N days (default 7, max 90)")
    me.add_argument("--event", default="",
                    help="Filter to one event name (try_submit / cli_init / cli_hunt / cli_outreach)")
    me.add_argument("--format", choices=("table", "json"), default="table")
    me.set_defaults(func=cmd_metrics)

    out = sub.add_parser("outreach", help="Send personalised cold emails to recent leads (find→send loop closer)")
    out.add_argument("action", nargs="?", default="send",
                     choices=("send",),
                     help="Action (currently only 'send')")
    out.add_argument("--top", type=int, default=5,
                     help="Send to top N leads ranked by fit_score (default 5)")
    out.add_argument("--max", dest="max_send", type=int, default=0,
                     help="Daily safety cap (0 = same as --top)")
    out.add_argument("--template", default="",
                     help="Path to template file: 'Subject: ...' line + body. {org_name} {contact_name} {evidence_quote} {event_name} {why_fit} {country} placeholders.")
    out.add_argument("--dry-run", action="store_true",
                     help="Render emails but don't send (always run this first)")
    out.add_argument("--research-above", type=float, default=0.0,
                     metavar="SCORE",
                     help=("Before sending, run `huntova research` on each "
                           "lead whose fit_score >= SCORE so the opener "
                           "references their last 14 pages of content. "
                           "Try 8.0 to research only the absolute top tier; "
                           "5.0 to research most qualifying leads. "
                           "0 = skip (default)."))
    out.set_defaults(func=cmd_outreach)

    hi = sub.add_parser("history", help="Show recent hunt runs from local DB")
    hi.add_argument("--limit", type=int, default=10, help="How many runs to show (default 10)")
    hi.add_argument("--format", choices=("table", "json"), default="table",
                    help="Output format (default: table)")
    hi.set_defaults(func=cmd_history)

    ld = sub.add_parser("lead", help="Print full detail for one lead by id (or partial org name)")
    ld.add_argument("id_or_query", help="Lead id (e.g. L3) or partial org name with --by-org")
    ld.add_argument("--by-org", action="store_true", help="Treat the query as a partial org-name match")
    ld.add_argument("--first", action="store_true", help="Pick the first match if --by-org returns multiple")
    ld.add_argument("--format", choices=("text", "json"), default="text", help="Output format")
    ld.set_defaults(func=cmd_lead)

    # `huntova research <lead-id>` — deep-research one lead. Re-crawls
    # 14 pages from the lead's website (vs 3 in the standard hunt
    # qualify pass), passes the full text into the AI with an
    # explicit "find the most specific personal hook" prompt, and
    # rewrites email_subject + email_body with the result. The
    # previous draft archives into rewrite_history so revert is one
    # click in the dashboard.
    qs = sub.add_parser(
        "quickstart",
        help="30-second interactive walkthrough — pick a playbook, run first hunt, preview drafts",
        description=("Single-command demo path for new users. "
                     "Picks a playbook from a menu, runs a 5-lead hunt, "
                     "shows the top fits, and prints the exact "
                     "follow-on commands. Pre-flights `huntova onboard`."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    qs.set_defaults(func=cmd_quickstart)

    rs = sub.add_parser(
        "research",
        help="deep-research one lead and rewrite the opener",
        epilog=("Examples:\n"
                "  huntova research L17\n"
                "  huntova research L17 --pages 20 --tone consultative\n"
                "  huntova research --batch 10 --above 8\n"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    rs.add_argument("lead_id", nargs="?", default="",
                    help="Lead id (e.g. L17). Omit when using --batch.")
    rs.add_argument("--pages", default="14",
                    help="Pages to crawl per lead [14, max 25]")
    rs.add_argument("--tone", default="",
                    help="Override tone (friendly / consultative / broadcast / warm / formal)")
    rs.add_argument("--batch", type=int, default=0, metavar="N",
                    help=("Research the top N un-researched leads instead of "
                          "a single lead_id. Skips leads already researched, "
                          "leads without org_website, and leads with email_status "
                          "in (replied, lost, won, ignored)."))
    rs.add_argument("--above", type=float, default=0.0, metavar="SCORE",
                    help="With --batch, only research leads with fit_score >= SCORE [0]")
    rs.set_defaults(func=cmd_research)

    # `huntova memory {search|inspect|recent|stats}` — pattern adapted
    # for the local lead-gen corpus. Implementation lives in cli_memory.py to keep this
    # file under the soft size cap.
    try:
        import cli_memory as _cli_memory
        _cli_memory.register(sub)
    except Exception as _mem_err:  # pragma: no cover — never block CLI boot
        sys.stderr.write(f"[huntova] memory subcommand unavailable: {_mem_err}\n")

    # `huntova migrate {from-csv|from-apollo|from-clay|from-hunter|stats}` —
    # bulk-import existing prospect lists. Implementation lives in
    # cli_migrate.py for the same size-cap reason.
    try:
        import cli_migrate as _cli_migrate
        _cli_migrate.register(sub)
    except Exception as _mig_err:  # pragma: no cover — never block CLI boot
        sys.stderr.write(f"[huntova] migrate subcommand unavailable: {_mig_err}\n")

    # `huntova approve {queue|diff|<id>|--top|--reject}` — manual-approval
    # queue for high-fit leads before outreach send releases the email.
    # Implementation lives in cli_approve.py for the same size-cap reason.
    try:
        import cli_approve as _cli_approve
        _cli_approve.register(sub)
    except Exception as _app_err:  # pragma: no cover — never block CLI boot
        sys.stderr.write(f"[huntova] approve subcommand unavailable: {_app_err}\n")

    # `huntova logs {tail|hunt|daemon|filter}` — unified log viewer for
    # debugging a hunt. Implementation lives in cli_logs.py.
    try:
        import cli_logs as _cli_logs
        _cli_logs.register(sub)
    except Exception as _logs_err:  # pragma: no cover — never block CLI boot
        sys.stderr.write(f"[huntova] logs subcommand unavailable: {_logs_err}\n")

    # `huntova inbox {setup|check|watch}` — IMAP reply-detection. Polls
    # the user's mailbox, matches incoming `In-Reply-To` headers to
    # outbound message-ids stored on the lead row by
    # `huntova outreach send`, flips matched leads to
    # email_status=replied + writes a 'good' feedback signal so the
    # next DNA refinement learns from the win.
    try:
        import cli_inbox as _cli_inbox
        _cli_inbox.register(sub)
    except Exception as _inbox_err:  # pragma: no cover — never block CLI boot
        sys.stderr.write(f"[huntova] inbox subcommand unavailable: {_inbox_err}\n")

    # `huntova sequence {run|status|pause}` — 3-step follow-up cadence
    # (Day +4 bump, Day +9 final). Auto-pauses leads that `huntova
    # inbox watch` flagged as replied. Pairs with `huntova outreach
    # send` (which sets _seq_step=1 on initial send).
    try:
        import cli_sequence as _cli_sequence
        _cli_sequence.register(sub)
    except Exception as _seq_err:  # pragma: no cover — never block CLI boot
        sys.stderr.write(f"[huntova] sequence subcommand unavailable: {_seq_err}\n")

    # `huntova pulse` — weekly self-coaching summary. Reads existing
    # tables (no schema bump), prints next-action suggestions.
    try:
        import cli_pulse as _cli_pulse
        _cli_pulse.register(sub)
    except Exception as _pulse_err:  # pragma: no cover — never block CLI boot
        sys.stderr.write(f"[huntova] pulse subcommand unavailable: {_pulse_err}\n")

    # `huntova schedule print` — emit launchd / systemd / cron snippet
    # for running sequence + inbox + pulse once per day. Doesn't
    # auto-install; user copies the snippet into the right place so
    # we don't silently persist anything on their machine.
    try:
        import cli_schedule as _cli_schedule
        _cli_schedule.register(sub)
    except Exception as _sch_err:  # pragma: no cover — never block CLI boot
        sys.stderr.write(f"[huntova] schedule subcommand unavailable: {_sch_err}\n")

    # `huntova remote {setup|test|start|status|stop|notify}` — Telegram
    # bot bridge so you can drive huntova from your phone. Long-polls
    # Telegram, routes inbound messages to the same /api/chat dispatcher
    # the dashboard uses — phone-as-remote without a mobile UI.
    try:
        import cli_remote as _cli_remote
        _cli_remote.register(sub)
    except Exception as _remote_err:  # pragma: no cover — never block CLI boot
        sys.stderr.write(f"[huntova] remote subcommand unavailable: {_remote_err}\n")

    # `huntova teach {<interactive>|--import <csv>|status}` — guided
    # "show the agent what good leads look like" flow. Records
    # lead_feedback rows + triggers DNA refinement every 10 signals
    # (same code path as the dashboard's good-fit/bad-fit buttons).
    # Implementation lives in cli_teach.py for the same size-cap reason.
    try:
        import cli_teach as _cli_teach
        _cli_teach.register(sub)
    except Exception as _teach_err:  # pragma: no cover — never block CLI boot
        sys.stderr.write(f"[huntova] teach subcommand unavailable: {_teach_err}\n")

    # `huntova benchmark {run|compare|fixtures}` — synthetic-hunt provider
    # quality benchmark (no quota burn). Implementation lives in cli_benchmark.py.
    try:
        import cli_benchmark as _cli_benchmark
        _cli_benchmark.register(sub)
    except Exception as _bench_err:  # pragma: no cover — never block CLI boot
        sys.stderr.write(f"[huntova] benchmark subcommand unavailable: {_bench_err}\n")

    # Grouped `--help` output. argparse's default subparser
    # action lists 31+ commands alphabetically on a single wrapped line,
    # which overwhelms new users. We monkey-patch the top-level parser's
    # `format_help` to render category blocks instead. Per-subcommand help
    # (`huntova <cmd> --help`) is unaffected — this only touches the
    # top-level format_help path.
    _attach_grouped_help(p, sub)

    return p


# Category map for the grouped `huntova --help` output. Order here is the
# order users see on screen. Every key in `sub.choices` MUST live in
# exactly one bucket; anything missing falls into "Other" defensively.
_HELP_CATEGORIES: list[tuple[str, tuple[str, ...]]] = [
    ("Getting started", ("onboard", "quickstart", "doctor", "status", "version")),
    ("Daily use",       ("serve", "chat", "hunt", "ls", "lead", "memory",
                          "teach", "history", "security")),
    ("Outreach",        ("approve", "outreach", "research", "sequence",
                          "inbox", "pulse", "examples", "recipe",
                          "share", "migrate")),
    ("Plugins / customization", ("plugins", "config")),
    ("Daemon / ops",    ("daemon", "schedule", "test-integrations", "logs",
                          "metrics", "cloud", "benchmark")),
    ("Utility",         ("update", "init", "rm", "completion", "install-completion",
                          "telemetry", "export")),
]


def _attach_grouped_help(parser: argparse.ArgumentParser,
                         sub: argparse._SubParsersAction) -> None:
    """Replace `parser.format_help` with a category-grouped renderer.

    Falls back to argparse's default if anything goes sideways so we
    never block `huntova --help` on a formatting error.
    """
    _default_format_help = parser.format_help

    def _grouped_format_help() -> str:
        try:
            choices = sub.choices  # OrderedDict: name -> ArgumentParser
            if not choices:
                return _default_format_help()

            # Bucket every choice. Track seen-set so we can dump the
            # leftovers into "Other" defensively.
            seen: set[str] = set()
            buckets: list[tuple[str, list[str]]] = []
            for label, names in _HELP_CATEGORIES:
                present = [n for n in names if n in choices]
                if present:
                    buckets.append((label, present))
                    seen.update(present)
            leftovers = [n for n in choices.keys() if n not in seen]
            if leftovers:
                buckets.append(("Other", leftovers))

            # Width budget: longest subcommand name + 2-space gutter.
            name_w = max((len(n) for n in choices.keys()), default=12)
            name_w = max(name_w, 12)

            lines: list[str] = []
            lines.append("usage: huntova [-h] [--version] <command> [...]")
            lines.append("")
            if parser.description:
                lines.append(parser.description)
                lines.append("")
            lines.append("Run `huntova <command> --help` for per-command flags.")
            lines.append("")

            for label, names in buckets:
                lines.append(f"{label}:")
                for n in names:
                    sp = choices[n]
                    # `sp.description` is rarely set; the help text is on
                    # the choice action instead.
                    help_text = ""
                    for action in sub._choices_actions:  # type: ignore[attr-defined]
                        if getattr(action, "dest", None) == n:
                            help_text = action.help or ""
                            break
                    if not help_text:
                        help_text = (sp.description or "").strip().splitlines()[0] if sp.description else ""
                    lines.append(f"  {n.ljust(name_w)}  {help_text}")
                lines.append("")

            lines.append("Top-level flags:")
            lines.append("  -h, --help     show this help message and exit")
            lines.append("  --version      print huntova version and exit")
            lines.append("")
            return "\n".join(lines)
        except Exception as e:  # pragma: no cover — never block --help
            # Round-9 finding #2: silent fallback was hiding bugs.
            # Surface to stderr so a regression is visible, then return
            # the default help so the user still sees something.
            print(f"[huntova] grouped --help failed ({type(e).__name__}: "
                  f"{str(e)[:80]}) — falling back to default formatter",
                  file=sys.stderr)
            return _default_format_help()

    parser.format_help = _grouped_format_help  # type: ignore[assignment]


def _smart_default_cmd(argv: list[str] | None) -> str:
    """Pick the right thing to do when the user runs `huntova` with
    no subcommand. Logic:

      * No AI provider configured anywhere → onboard (60-second wizard).
      * Onboarded but no leads / no recipes → quickstart
        (interactive playbook picker + first hunt).
      * Otherwise → serve (the dashboard, current behaviour).

    The state probes are best-effort and silent — if any of them
    raises, we fall back to `serve` so we never block the original
    "plain huntova boots the dashboard" UX.
    """
    if argv:
        return "serve"
    # Provider check
    try:
        os.environ.setdefault("APP_MODE", "local")
        _hydrate_env_from_local_config()
        from providers import list_available_providers
        if not (list_available_providers() or []):
            return "onboard"
    except Exception:
        return "serve"
    # Recipe / lead presence check — only when bootstrap succeeds.
    try:
        # Suppress bootstrap chatter so plain `huntova` doesn't print
        # `[huntova] bootstrap …` lines before deciding what to run.
        import io as _io
        import contextlib as _ctx
        with _ctx.redirect_stdout(_io.StringIO()), _ctx.redirect_stderr(_io.StringIO()):
            uid = _bootstrap_local_env()
        if uid is None:
            return "serve"
        import asyncio as _aio
        import db as _db
        leads = _aio.run(_db.get_leads(uid, limit=1)) or []
        if not leads:
            try:
                recipes = _aio.run(_db.list_hunt_recipes(uid)) or []
            except Exception:
                recipes = []
            if not recipes:
                return "quickstart"
    except Exception:
        return "serve"
    return "serve"


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.cmd:
        # State-aware default. Brand-new installs land in `quickstart`,
        # un-onboarded installs land in `onboard`, everything else
        # keeps the historical `huntova` → `huntova serve` mapping.
        chosen = _smart_default_cmd(argv)
        args = parser.parse_args([chosen] + (argv or []))
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
