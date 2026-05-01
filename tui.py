"""TUI primitives for huntova onboard + future interactive subcommands.

Inspired by OpenClaw's `@clack/prompts` shape (intro/outro/select/text/
password/confirm/progress) but built on top of Python's `questionary`.
Falls back to plain `input()` when questionary isn't installed or when
stdin isn't a TTY so CI / piped invocations still work.

NOT a copy of any specific OpenClaw code — this is an idiomatic Python
re-implementation of the abstract surface they use.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional


# ── Colour primitives ────────────────────────────────────────────


def _is_tty() -> bool:
    return sys.stdout.isatty() and sys.stdin.isatty()


def _ansi(s: str, code: str) -> str:
    if not sys.stdout.isatty():
        return s
    return f"\033[{code}m{s}\033[0m"


def bold(s: str) -> str: return _ansi(s, "1")
def dim(s: str) -> str: return _ansi(s, "2")
def red(s: str) -> str: return _ansi(s, "31")
def green(s: str) -> str: return _ansi(s, "32")
def yellow(s: str) -> str: return _ansi(s, "33")
def blue(s: str) -> str: return _ansi(s, "34")
def purple(s: str) -> str: return _ansi(s, "35")
def cyan(s: str) -> str: return _ansi(s, "36")


# ── Header / banner ───────────────────────────────────────────────


HUNTOVA_BANNER = r"""
 _   _ _   _ _   _ _____ _____ _   _ ___
| | | | | | | \ | |_   _|  _  | | | |  _ \
| |_| | | | |  \| | | | | | | | | | | |_) |
|  _  | |_| | |\  | | | | |_| | \_/ |  _ <
|_| |_|\___/|_| \_| |_|  \___/ \___/|_| \_\
""".strip("\n")


_TAGLINES = (
    "find your next 100 customers — without writing a single subject line.",
    "Apollo charges $99/mo. I charge whatever your AI provider charges.",
    "I read every prospect's website like a salesperson would. Apollo doesn't.",
    "lead gen, but the agent actually visits the page.",
    "if it can't quote the prospect's own copy back to them, it's not qualified.",
    "evidence-first prospecting — every fit score has a receipt.",
    "I run on YOUR machine. Your AI key. Your data. Your problem if it works.",
    "300 lines of Python and a stubborn refusal to ship fake data.",
    "default model: Claude. yes, the irony of an AI built using AI is not lost on me.",
    "13 AI providers, 1 SearXNG, 0 vendor lock-in.",
)


def _pick_tagline() -> str:
    import random
    return random.choice(_TAGLINES)


def print_banner(subtitle: str = "", show_tagline: bool = True) -> None:
    """Print the Huntova ASCII-art banner with subtitle. Skipped on
    non-TTY so log files don't get polluted.

    show_tagline: rotates a witty one-liner under the logo (mirrors
    OpenClaw's banner-tagline pattern). Disable for non-onboarding
    surfaces where the tagline is noise.
    """
    if not _is_tty():
        return
    print()
    print(purple(HUNTOVA_BANNER))
    print()
    if show_tagline:
        print(f"   {dim(_pick_tagline())}")
        print()
    if subtitle:
        print(f"  {bold(subtitle)}")
        print()


def config_summary_card(items: list[tuple[str, str]], title: str = "Existing config detected") -> None:
    """Boxed key-value card mirroring OpenClaw's `Existing config
    detected` panel. items is a list of (label, value) pairs.

    Renders:
        ╭─ Existing config detected ─╮
        │  workspace: …              │
        │  preferred_provider: …     │
        ╰────────────────────────────╯
    """
    if not items or not _is_tty():
        return
    label_w = max(len(k) for k, _ in items)
    val_w = max(len(str(v)) for _, v in items)
    inner_w = max(label_w + val_w + 4, len(title) + 6)
    print()
    print(f"  {dim('╭─')} {bold(title)} {dim('─' * (inner_w - len(title) - 4))}{dim('╮')}")
    for k, v in items:
        line = f"{k}: {v}".ljust(inner_w)
        print(f"  {dim('│')} {line} {dim('│')}")
    print(f"  {dim('╰' + '─' * (inner_w + 2) + '╯')}")
    print()


# ── intro / outro / note ──────────────────────────────────────────


def intro(title: str) -> None:
    """Open a wizard section. Mirrors clack's intro()."""
    print()
    print(f"  {purple('▸')} {bold(title)}")
    print()


def outro(message: str) -> None:
    """Close a wizard section."""
    print()
    print(f"  {green('●')} {message}")
    print()


def note(message: str, title: str = "") -> None:
    """Boxed informational note."""
    lines = message.split("\n")
    width = min(max((len(t) for t in lines), default=40), 70) + 4
    if title:
        print(f"  {dim('┌─')} {bold(title)} {dim('─' * max(0, width - len(title) - 6))}")
    else:
        print(f"  {dim('┌' + '─' * (width - 2) + '┐')}")
    for line in lines:
        print(f"  {dim('│')} {line}")
    print(f"  {dim('└' + '─' * (width - 2) + '┘')}")


def cancelled() -> None:
    """Print a friendly cancel message + exit cleanly."""
    print()
    print(f"  {yellow('●')} Setup cancelled. Re-run {cyan('huntova onboard')} when ready.")
    print()


# ── Prompts ───────────────────────────────────────────────────────


def _has_questionary() -> bool:
    try:
        import questionary  # noqa: F401
        return True
    except ImportError:
        return False


@dataclass
class SelectOption:
    value: str
    label: str
    hint: str = ""
    disabled: bool = False


def select(message: str, options: list[SelectOption],
           default: Optional[str] = None) -> Optional[str]:
    """Arrow-key select with hints. Returns the chosen value or None
    on Ctrl+C. Falls back to numbered prompt when questionary missing
    or non-TTY."""
    if not options:
        return None
    if _has_questionary() and _is_tty():
        try:
            import questionary
            choices = []
            for o in options:
                if o.disabled:
                    choices.append(questionary.Choice(
                        title=f"{o.label}  (disabled)",
                        value=o.value, disabled=o.hint or "unavailable"))
                else:
                    suffix = f"   — {dim(o.hint)}" if o.hint else ""
                    choices.append(questionary.Choice(
                        title=f"{o.label}{suffix}",
                        value=o.value))
            default_choice = next(
                (c for c, opt in zip(choices, options) if opt.value == default),
                None,
            )
            return questionary.select(
                message,
                choices=choices,
                default=default_choice,
                use_arrow_keys=True,
                use_shortcuts=False,
                use_indicator=True,
                qmark=cyan("?"),
            ).ask()
        except (KeyboardInterrupt, EOFError):
            return None
        except Exception:
            pass  # fall through to numbered prompt
    return _numbered_select_fallback(message, options, default)


def _numbered_select_fallback(message: str, options: list[SelectOption],
                                default: Optional[str]) -> Optional[str]:
    print(f"\n  {message}")
    for i, opt in enumerate(options, start=1):
        marker = green("●") if opt.value == default else " "
        hint = f"  {dim(opt.hint)}" if opt.hint else ""
        print(f"    {marker} {i}. {opt.label}{hint}")
    default_idx = next(
        (i for i, opt in enumerate(options, start=1) if opt.value == default),
        1,
    )
    try:
        raw = input(f"  Pick [1-{len(options)}, default {default_idx}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    if not raw:
        return options[default_idx - 1].value
    try:
        idx = int(raw)
        if 1 <= idx <= len(options) and not options[idx - 1].disabled:
            return options[idx - 1].value
    except ValueError:
        pass
    return options[default_idx - 1].value


def text(message: str, placeholder: str = "", default: str = "",
         validate: Optional[Callable[[str], Optional[str]]] = None) -> Optional[str]:
    """Free-text prompt with optional validation. Returns the value or
    None on Ctrl+C. validate(value) returns an error message string
    or None if valid."""
    if _has_questionary() and _is_tty():
        try:
            import questionary
            return questionary.text(
                message,
                default=default or "",
                validate=lambda v: True if (validate is None or not validate(v)) else validate(v),
                qmark=cyan("?"),
            ).ask()
        except (KeyboardInterrupt, EOFError):
            return None
        except Exception:
            pass
    suffix = f" [{default}]" if default else f" ({placeholder})" if placeholder else ""
    while True:
        try:
            raw = input(f"  {message}{suffix}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        value = raw or default
        if validate:
            err = validate(value)
            if err:
                print(f"  {red('✗')} {err}")
                continue
        return value


def password(message: str,
             validate: Optional[Callable[[str], Optional[str]]] = None) -> Optional[str]:
    """Hidden-input password prompt. Returns the value or None on Ctrl+C.

    Strips leading/trailing whitespace from the user's input. API keys
    are whitespace-sensitive — pasting `" sk-abc..."` (note the leading
    space many shells / paste buffers add) used to save the bogus
    string to the keychain, which then failed every auth call. Strip
    here so the keychain only ever holds clean values.
    """
    if _has_questionary() and _is_tty():
        try:
            import questionary
            ans = questionary.password(
                message,
                validate=lambda v: True if (validate is None or not validate(v)) else validate(v),
                qmark=cyan("?"),
            ).ask()
            return ans.strip() if isinstance(ans, str) else ans
        except (KeyboardInterrupt, EOFError):
            return None
        except Exception:
            pass
    import getpass
    while True:
        try:
            raw = getpass.getpass(f"  {message}: ")
        except (EOFError, KeyboardInterrupt):
            print()
            return None
        raw = raw.strip()
        if validate:
            err = validate(raw)
            if err:
                print(f"  {red('✗')} {err}")
                continue
        return raw


def confirm(message: str, default: bool = True) -> bool:
    """Yes/no prompt. Returns the answer (default on Ctrl+C)."""
    if _has_questionary() and _is_tty():
        try:
            import questionary
            # Stability fix (audit wave 29): questionary catches Ctrl+C
            # internally and returns None from .ask() rather than
            # raising KeyboardInterrupt — so the previous version's
            # `.ask() is True` evaluated `None is True` → False, silently
            # overriding the docstring's "default on Ctrl+C" promise.
            # Now: keep the legacy KeyboardInterrupt branch as a safety
            # net for older questionary versions that DO raise, but
            # treat ans=None as "cancelled, fall back to default".
            ans = questionary.confirm(
                message, default=default, qmark=cyan("?"),
            ).ask()
            return default if ans is None else bool(ans)
        except (KeyboardInterrupt, EOFError):
            return default
        except Exception:
            pass
    suffix = "[Y/n]" if default else "[y/N]"
    try:
        raw = input(f"  {message} {suffix}: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    if not raw:
        return default
    return raw in ("y", "yes")


# ── Progress spinner ──────────────────────────────────────────────


class Spinner:
    """Animated spinner that runs a callable in a thread. Mirrors
    clack's progress() shape with .update() and .stop()."""
    SPINNER_CHARS = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, label: str):
        self.label = label
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._tty = _is_tty()

    def start(self) -> "Spinner":
        if not self._tty:
            print(f"  {dim('▸')} {self.label}…", flush=True)
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()
        return self

    def _spin(self) -> None:
        i = 0
        while not self._stop.is_set():
            ch = self.SPINNER_CHARS[i % len(self.SPINNER_CHARS)]
            sys.stdout.write(f"\r  {cyan(ch)} {self.label}…")
            sys.stdout.flush()
            time.sleep(0.08)
            i += 1

    def update(self, label: str) -> None:
        self.label = label

    def stop(self, message: str = "", success: bool = True) -> None:
        if self._thread:
            self._stop.set()
            self._thread.join(timeout=0.5)
        if not self._tty:
            print(f"  {green('✓') if success else red('✗')} {message or self.label}")
            return
        sys.stdout.write("\r  ")
        sys.stdout.write(" " * (len(self.label) + 8))  # clear line
        sys.stdout.write(f"\r  {green('✓') if success else red('✗')} {message or self.label}\n")
        sys.stdout.flush()


def with_spinner(label: str, fn: Callable[[], Any]) -> tuple[bool, Any]:
    """Run fn() with a spinner. Returns (ok, result_or_exception)."""
    sp = Spinner(label).start()
    try:
        result = fn()
        sp.stop(success=True)
        return True, result
    except Exception as e:
        sp.stop(message=f"{label} — {type(e).__name__}: {str(e)[:80]}", success=False)
        return False, e


# ── Browser launcher (SSH / WSL / DISPLAY-aware) ──────────────────


def detect_browser_open_support() -> tuple[bool, str]:
    """Return (can_open, reason). Mirrors OpenClaw's resolveBrowserOpenCommand
    pattern: detects SSH-without-display, WSL, missing platform binaries.
    Idiomatic Python re-implementation, not a code copy."""
    is_ssh = any(os.environ.get(v) for v in ("SSH_CLIENT", "SSH_TTY", "SSH_CONNECTION"))
    has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    sysname = platform.system().lower()

    if is_ssh and not has_display and sysname != "windows":
        return False, "ssh-no-display"
    if sysname == "darwin":
        if shutil.which("open"):
            return True, "darwin-open"
        return False, "missing-open"
    if sysname == "windows":
        return True, "windows-rundll32"
    if sysname == "linux":
        is_wsl = "microsoft" in (platform.release() or "").lower()
        if is_wsl and shutil.which("wslview"):
            return True, "wsl-wslview"
        if not has_display and not is_wsl:
            return False, "no-display"
        if shutil.which("xdg-open"):
            return True, "linux-xdg-open"
        return False, "missing-xdg-open"
    return False, "unsupported-platform"


def open_url(url: str, timeout_s: float = 5.0) -> bool:
    """Open a URL in the user's default browser if possible. Returns
    True on success, False if no browser-launch path is available."""
    if not url.startswith(("http://", "https://")):
        return False
    if os.environ.get("HV_NO_BROWSER") or os.environ.get("CI"):
        return False
    can, reason = detect_browser_open_support()
    if not can:
        return False
    sysname = platform.system().lower()
    try:
        if sysname == "darwin":
            subprocess.Popen(["open", url],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        if sysname == "windows":
            system_root = os.environ.get("SystemRoot") or "C:\\Windows"
            rundll32 = os.path.join(system_root, "System32", "rundll32.exe")
            subprocess.Popen([rundll32, "url.dll,FileProtocolHandler", url],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        if sysname == "linux":
            cmd = "wslview" if reason == "wsl-wslview" else "xdg-open"
            subprocess.Popen([cmd, url],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
    except Exception:
        return False
    return False
