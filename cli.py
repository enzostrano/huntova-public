"""
Huntova CLI — OpenClaw-style entry point.

Usage:
    huntova               # alias for `huntova serve`
    huntova serve         # boot local FastAPI on 127.0.0.1:5000, open browser
    huntova init          # interactive first-run wizard (BYOK key, defaults)
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

VERSION = "0.1.0a1120"
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

    Returns the preferred_provider name (or "gemini" default).
    """
    preferred = "gemini"
    # 1) secrets_store (keychain or encrypted file)
    try:
        from secrets_store import get_secret
        for env_var in ("HV_GEMINI_KEY", "HV_ANTHROPIC_KEY", "HV_OPENAI_KEY"):
            if not os.environ.get(env_var):
                val = get_secret(env_var)
                if val:
                    os.environ[env_var] = val
    except Exception:
        pass
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
    # Friendly heads-up if no key is configured yet.
    has_key = any(
        os.environ.get(v)
        for v in ("HV_GEMINI_KEY", "HV_ANTHROPIC_KEY", "HV_OPENAI_KEY")
    )
    if not has_key:
        print("[huntova] no API key configured — run `huntova init` first or set HV_GEMINI_KEY")
    import uvicorn  # noqa: F401  — import after env-var setup
    host = args.host or DEFAULT_HOST
    port = int(args.port or DEFAULT_PORT)
    backend = "PostgreSQL" if os.environ.get("DATABASE_URL") else "SQLite (~/.local/share/huntova/db.sqlite)"
    print(f"[huntova] provider: {preferred}    storage: {backend}")
    print(f"[huntova] starting local server on http://{host}:{port}")
    if not args.no_browser:
        try:
            webbrowser.open_new_tab(f"http://{host}:{port}/")
        except Exception:
            pass
    uvicorn.run("server:app", host=host, port=port, log_level="info")
    return 0


_PROVIDERS = (
    ("gemini", "Google Gemini (default — fastest, free tier available)",
     "https://aistudio.google.com/apikey", "HV_GEMINI_KEY"),
    ("anthropic", "Anthropic Claude (highest quality scoring)",
     "https://console.anthropic.com/settings/keys", "HV_ANTHROPIC_KEY"),
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

    # 2. Server reachability (probes the default port)
    try:
        import urllib.request, socket
        socket.setdefaulttimeout(0.5)
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
    """Read preferred_provider from config.toml. Returns 'gemini' as
    fallback when unset."""
    try:
        import tomllib
        cfg_path = _config_dir() / "config.toml"
        if cfg_path.exists():
            data = tomllib.loads(cfg_path.read_text())
            return (data.get("preferred_provider") or "gemini").strip().lower()
    except Exception:
        pass
    return "gemini"


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
            print(f"[huntova] no config at {cfg_path} — run `huntova onboard`.")
            return 1
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

    print(f"[huntova] unknown config subcommand {sub!r} — try show / edit / get / set / path", file=sys.stderr)
    return 1


def _default_config_template() -> str:
    return """# Huntova configuration
# Non-secret settings only. API keys live in your OS keychain
# (or ~/.config/huntova/secrets.enc fallback).

# AI provider used for scoring + email drafting
preferred_provider = "gemini"

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
        results.append({"name": "providers.list", "ok": False, "msg": str(e)[:80]})
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

    # 2. SearXNG
    searxng = os.environ.get("SEARXNG_URL", "").strip() or "http://127.0.0.1:8888"
    def _searxng_probe():
        import urllib.request, json as _j
        url = searxng.rstrip("/") + "/search?q=huntova_test&format=json"
        with urllib.request.urlopen(url, timeout=4) as r:
            data = _j.loads(r.read())
        return f"reachable, {len(data.get('results', []))} results"
    ok, val = with_spinner(f"SearXNG: {searxng}", _searxng_probe)
    results.append({"name": "searxng", "ok": ok, "msg": (val if ok else type(val).__name__)[:60]})

    # 3. Playwright
    def _playwright_probe():
        from importlib.util import find_spec
        if not find_spec("playwright"):
            raise RuntimeError("playwright not installed")
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
        results.append({"name": "smtp", "ok": ok, "msg": (val if ok else type(val).__name__)[:80]})

    if args.format == "json":
        import json as _json
        print(_json.dumps({"results": results,
                            "ok_count": sum(1 for r in results if r["ok"]),
                            "fail_count": sum(1 for r in results if not r["ok"])},
                           indent=2, default=str))
        return 1 if any(not r["ok"] for r in results) else 0

    # Pretty summary
    print()
    print(f"  {bold('Integration test results:')}")
    ok_count = sum(1 for r in results if r["ok"])
    fail_count = len(results) - ok_count
    if fail_count == 0:
        print(f"  {green('●')} all {len(results)} integrations passed")
    else:
        print(f"  {yellow('●')} {ok_count}/{len(results)} passed, {red(str(fail_count))} failed")
    print()
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

    Inspired by openclaw/openclaw's `openclaw onboard`. Three phases:
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
        huntova onboard --browser    # skip TUI, open the web wizard
        huntova onboard --no-launch  # don't auto-start `huntova serve` at the end
        huntova onboard --no-prompt  # CI / scripted (uses env keys)
    """
    return _onboard_v2(args)


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
        # Hand off to cmd_serve which opens the browser automatically
        class _Args:
            host = None; port = port; no_browser = False
        return cmd_serve(_Args())

    # ── Banner ────────────────────────────────────────────────────
    print_banner("First-run setup · ~60 seconds")

    # ── Step 1: Filesystem ────────────────────────────────────────
    intro("Step 1 of 3 — Filesystem")
    cfg_path = _config_dir() / "config.toml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    if not cfg_path.exists():
        cfg_path.write_text("# Huntova config\npreferred_provider = \"gemini\"\n")
        if os.name != "nt":
            try: os.chmod(cfg_path, 0o600)
            except OSError: pass
    print(f"    {green('✓')} config dir   {dim(str(cfg_path.parent))}")
    try:
        import db_driver as _dbd
        db_path = _dbd._local_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        print(f"    {green('✓')} data dir     {dim(str(db_path.parent))}")
    except Exception as e:
        print(f"    {red('✗')} data dir     {type(e).__name__}: {e}")
    try:
        from secrets_store import _backend_label
        backend = _backend_label()
        backend_color = green if backend == "keyring" else yellow
        print(f"    {backend_color('✓')} secrets      {dim(backend)}")
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
        SelectOption("gemini",     "🟦 Google Gemini",     "free tier · ~$0.005 / 10 leads · default"),
        SelectOption("anthropic",  "🟧 Anthropic Claude",  "highest accuracy · ~$0.04 / 10 leads"),
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

    chosen = select("Pick a provider:", options, default="gemini")
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
    outro("Setup complete. Run `huntova serve` whenever you want the dashboard.")
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
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception:
            # Fall back to direct cmd_serve invocation in this process
            class _Args:
                host = None; port = port; no_browser = False
            return cmd_serve(_Args())
        import time, webbrowser
        time.sleep(1.5)  # give server a moment to bind
        try:
            webbrowser.open_new_tab(f"http://127.0.0.1:{port}/setup")
        except Exception:
            pass
        print(f"\n  {green('✓')} Web wizard opened. Press Ctrl+C here when you've finished.")
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            print()
            return 0

    # ── Step 1: Filesystem ────────────────────────────────────────
    print(f"  {bold('Step 1 of 3 — Filesystem')}")
    cfg_path = _config_dir() / "config.toml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    if not cfg_path.exists():
        cfg_path.write_text(
            "# Huntova config\n"
            'preferred_provider = "gemini"\n'
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
    print(f"    {dim('Run `huntova serve` to open the dashboard whenever you want.')}")
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
            "# if keyring isn't installed). Set HV_GEMINI_KEY / HV_ANTHROPIC_KEY /",
            "# HV_OPENAI_KEY in your env to start hunting, or run `huntova init --wizard`",
            "# for a friendly setup.\n",
            'preferred_provider = "gemini"',
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
        print("    export HV_GEMINI_KEY=...      # https://aistudio.google.com/apikey")
        print("    export HV_ANTHROPIC_KEY=...   # https://console.anthropic.com/settings/keys")
        print("    export HV_OPENAI_KEY=...      # https://platform.openai.com/api-keys")
        print("")
        print("  then run:")
        print("    huntova hunt --max-leads 5")
        print("")
        if not _telemetry_consent():
            print("  (Optional: `huntova telemetry enable` to share anonymous usage stats.)")
        metrics_emit("cli_init", {"provider": "gemini", "had_key": False, "wizard": False})
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
            print("providers: ✗ (none — run `huntova init` to add a key)")
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
                return 0
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
    return 1 if fail else 0


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


def cmd_update(args: argparse.Namespace) -> int:
    """Self-upgrade via pipx (or pip --user as fallback)."""
    import shutil
    import subprocess
    if shutil.which("pipx"):
        cmd = ["pipx", "upgrade", "huntova"]
    elif shutil.which("pip"):
        cmd = [sys.executable, "-m", "pip", "install", "--user", "--upgrade", "huntova"]
    else:
        print("[huntova] neither pipx nor pip found — install pipx and re-run")
        return 1
    print(f"[huntova] running: {' '.join(cmd)}")
    try:
        return subprocess.call(cmd)
    except KeyboardInterrupt:
        return 130


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
    leads = _asyncio.run(_db.get_leads(user_id, limit=max(1, int(args.limit) * 4 if args.filter else int(args.limit))))
    if not leads:
        print("[huntova] no leads yet — run `huntova hunt` first.")
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
        sys.stdout.write(_json.dumps(leads, indent=2, default=str))
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


def cmd_completion(args: argparse.Namespace) -> int:
    """Print shell completion code for bash, zsh, or fish."""
    shell = (args.shell or "bash").lower()
    if shell == "bash":
        sys.stdout.write(_BASH_COMPLETION)
    elif shell == "zsh":
        sys.stdout.write(_ZSH_COMPLETION)
    elif shell == "fish":
        sys.stdout.write(_FISH_COMPLETION)
    else:
        print(f"[huntova] unknown shell {shell!r} — supported: bash, zsh, fish", file=sys.stderr)
        return 1
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
        if not args.force and not verified:
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
        if target.exists() and not args.force:
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
        # Build a synthetic args namespace for cmd_hunt.
        countries_csv = ",".join(cfg.get("countries") or [])
        hunt_args = argparse.Namespace(
            countries=countries_csv,
            max_leads=int(cfg.get("max_leads") or 0),
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
        # Accept both HTML and .json shapes
        json_url = url if url.endswith(".json") else (url.rstrip("/") + ".json")
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

    print(f"[huntova] unknown recipe subcommand {sub!r} — try ls / save / run / rm / inspect / adapt / publish / import-url", file=sys.stderr)
    return 1


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
    cur = conn.cursor()
    sql = drv.translate_sql(
        "SELECT id, status, leads_found, ai_calls, queries_done, queries_total, "
        "started_at, ended_at FROM agent_runs WHERE user_id = %s "
        "ORDER BY started_at DESC LIMIT %s"
    )
    cur.execute(sql, (user_id, max(1, int(args.limit))))
    rows_raw = cur.fetchall()
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
            return 0
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
    """
    sub = (args.subcommand or "mint").lower()

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
        share_url = f"{base}/h/{slug}"
        color = sys.stdout.isatty()
        bold = (lambda s: f"\033[1m{s}\033[0m") if color else (lambda s: s)
        green = (lambda s: f"\033[32m{s}\033[0m") if color else (lambda s: s)
        dim = (lambda s: f"\033[2m{s}\033[0m") if color else (lambda s: s)
        print(f"\n  {bold('share status:')} {slug}")
        print(f"  {dim('url:')}     {share_url}")
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
    has_key = any(
        os.environ.get(v)
        for v in ("HV_GEMINI_KEY", "HV_ANTHROPIC_KEY", "HV_OPENAI_KEY")
    )
    # --dry-run skips the key requirement — pure wiring smoke test.
    if not has_key and not args.dry_run:
        print("[huntova] no API key configured. Run `huntova init` first.", file=sys.stderr)
        return 2

    countries = [c.strip() for c in (args.countries or "").split(",") if c.strip()]
    fork_title = ""
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
        info = print  # always to stdout in this preflight (json_mode hasn't been bound yet)
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

    # Output mode: --json emits one JSON object per line on stdout,
    # nothing else. Errors and status messages still go to stderr so
    # `huntova hunt --json | jq` works cleanly.
    json_mode = bool(args.json)
    info = (lambda *a, **k: print(*a, **k, file=sys.stderr)) if json_mode else print

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
        sys.stdout.write(_json.dumps(obj, default=str))
        sys.stdout.write("\n")
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
    try:
        result = _asyncio.run(agent_runner.start_agent(
            user_id=user_id,
            user_email=user["email"],
            user_tier=user.get("tier", "local"),
            config={"countries": countries},
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
        if existing and not args.force:
            print(f"[huntova] recipe {name!r} already exists — re-run with --force to overwrite.", file=sys.stderr)
            return 1
        spec = _BUNDLED_EXAMPLES[name]
        _asyncio.run(_db.save_hunt_recipe(
            user_id, name,
            description=spec["description"],
            config=spec["config"],
        ))
        print(f"\n{green('✓')} installed example recipe {bold(name)}\n")
        print(f"  Run it with:")
        print(f"    {dim('$')} huntova recipe run {name}")
        print(f"  Inspect first:")
        print(f"    {dim('$')} huntova recipe inspect {name}\n")
        metrics_emit("cli_example_install", {"name": name})
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

    # SMTP must be configured for real sends. --dry-run skips the check.
    smtp_ready = all(os.environ.get(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD"))
    if not smtp_ready and not args.dry_run:
        print("[huntova] SMTP not configured. Set SMTP_HOST / SMTP_USER / SMTP_PASSWORD,", file=sys.stderr)
        print("           or use --dry-run to preview without sending.", file=sys.stderr)
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

    # Filter + sort. Skip leads without contact_email AND skip duplicates.
    candidates = []
    seen_emails = set()
    for ld in leads:
        email = (ld.get("contact_email") or "").strip().lower()
        if not email or email in seen_emails:
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
        except (KeyError, IndexError):
            return template

    print(f"\n{bold(f'huntova outreach — {len(targets)} email(s) to send' + (' (dry-run)' if args.dry_run else ''))}\n")
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
            _send_email_sync(to, subject, html, plain_body=body)
            sent += 1
            try:
                _asyncio.run(_db.save_lead_action(
                    user_id, lead.get("lead_id") or "?", "email_sent",
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
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("serve", help="Run the local web app on 127.0.0.1")
    s.add_argument("--host", default=None, help=f"bind host (default {DEFAULT_HOST})")
    s.add_argument("--port", default=None, type=int, help=f"bind port (default {DEFAULT_PORT})")
    s.add_argument("--no-browser", action="store_true", help="don't auto-open the browser")
    s.set_defaults(func=cmd_serve)

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

    cf = sub.add_parser("config",
                        help="Show / edit the config (path / show / get / set / edit)")
    cf.add_argument("subcommand", nargs="?", default="show",
                     choices=("show", "edit", "get", "set", "path"),
                     help="Action (default: show)")
    cf.add_argument("key", nargs="?", default="",
                     help="Config key (for get / set)")
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

    ob = sub.add_parser("onboard",
                        help="First-run wizard — picks provider, saves key, opens dashboard (RECOMMENDED for new users)")
    ob.add_argument("--browser", action="store_true",
                    help="Skip the TUI and open the web wizard at /setup")
    ob.add_argument("--no-launch", action="store_true",
                    help="Don't auto-start `huntova serve` after setup completes")
    ob.add_argument("--no-prompt", action="store_true",
                    help="Non-interactive mode (CI / scripted) — fails if key not in env")
    ob.add_argument("--force", action="store_true",
                    help="Re-prompt even if a key is already saved")
    ob.set_defaults(func=cmd_onboard)

    d = sub.add_parser("doctor", help="Diagnostic dump")
    d.add_argument("--quick", action="store_true",
                   help="Skip live network probes (AI ping + SearXNG round-trip). For CI / smoke runs.")
    d.set_defaults(func=cmd_doctor)

    v = sub.add_parser("version", help="Print version")
    v.set_defaults(func=cmd_version)

    u = sub.add_parser("update", help="Upgrade Huntova to the latest version")
    u.set_defaults(func=cmd_update)

    h = sub.add_parser("hunt", help="One-shot headless hunt (no browser needed)")
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
    ls.set_defaults(func=cmd_ls)

    ex = sub.add_parser("export", help="Export saved leads (CSV or JSON) to stdout")
    ex.add_argument("--format", choices=("csv", "json"), default="csv",
                    help="Output format (default: csv)")
    ex.set_defaults(func=cmd_export)

    sh = sub.add_parser("share", help="Mint or query share links (status <slug> for view count)")
    sh.add_argument("subcommand", nargs="?", default="mint",
                    choices=("mint", "status"),
                    help="`mint` (default) creates a new share; `status` <slug> shows views")
    sh.add_argument("slug", nargs="?", default="",
                    help="Slug or /h/<slug> URL (for `status`)")
    sh.add_argument("--top", type=int, default=10, help="How many top-fit leads to include (default 10) — mint only")
    sh.add_argument("--title", default="", help="Optional title for the public page — mint only")
    sh.set_defaults(func=cmd_share)

    rm = sub.add_parser("rm", help="Delete a lead permanently from local DB")
    rm.add_argument("lead_id", help="The lead_id to delete (e.g. L3)")
    rm.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompt")
    rm.set_defaults(func=cmd_rm)

    rc = sub.add_parser("recipe", help="Save / list / run / inspect / adapt / publish / import-url")
    rc.add_argument("subcommand",
                    choices=("ls", "save", "run", "rm", "inspect", "adapt",
                             "publish", "import-url"),
                    help="Action: ls / save / run / rm / inspect / adapt <name> / publish <name> / import-url <url>")
    rc.add_argument("name", nargs="?", default="",
                    help="Recipe name (for save/run/rm/publish) or URL (for import-url)")
    rc.add_argument("--countries", default="", help="Comma-separated country list (save)")
    rc.add_argument("--max-leads", type=int, default=0, help="Stop after N leads (save)")
    rc.add_argument("--queries", default="", help="Pipe-separated pre-set queries (save)")
    rc.add_argument("--description", default="", help="Optional human-readable note (save)")
    rc.add_argument("--dry-run", action="store_true", help="Walk run setup without firing the agent")
    rc.add_argument("--force", action="store_true", help="adapt: lower the outcomes threshold from 20 to 5")
    rc.add_argument("--format", choices=("table", "json"), default="table", help="Output format (ls/inspect)")
    rc.set_defaults(func=cmd_recipe)

    pl = sub.add_parser("plugins", help="List discovered plugins (or `search` / `create`)")
    pl.add_argument("subcommand", nargs="?", default="list",
                    choices=("list", "create", "search", "install", "contribute"),
                    help="`list` (default) / `create <name>` / `search [query]` / `install <name>` / `contribute`")
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

    tm = sub.add_parser("telemetry", help="Opt-in anonymous usage telemetry (enable/disable/status)")
    tm.add_argument("action", nargs="?", default="status",
                    choices=("enable", "disable", "status"),
                    help="enable / disable / status (default: status)")
    tm.set_defaults(func=cmd_telemetry)

    ex_p = sub.add_parser("examples", help="Bundled starter recipes — `ls` or `install <name>`")
    ex_p.add_argument("subcommand", nargs="?", default="ls",
                     choices=("ls", "install"),
                     help="`ls` (default) or `install <name>`")
    ex_p.add_argument("name", nargs="?", default="",
                     help="Example name (for `install`)")
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

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.cmd:
        # Default to `serve` so plain `huntova` Just Works.
        args = parser.parse_args(["serve"] + (argv or []))
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
