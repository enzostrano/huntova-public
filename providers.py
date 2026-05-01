"""
Huntova LLM provider abstraction (BYOK).

Replaces direct `OpenAI(base_url=..., api_key=...)` calls scattered in
app.py / server.py with a single `get_provider(...)` entrypoint that
can return Gemini, Anthropic Claude, or OpenAI implementations.

Resolution priority (highest first) for the API key:
1. Per-user settings dict (cloud mode, persisted in user_settings.data)
2. secrets_store (CLI keychain / encrypted file in local mode)
3. Environment variable (HV_GEMINI_KEY / HV_ANTHROPIC_KEY / HV_OPENAI_KEY)

`preferred_provider` field in settings drives the choice; if unset we
pick whichever has a usable key in the order gemini → anthropic →
openai (Gemini stays default because it's what Huntova has already
been benchmarked against).

All providers expose a uniform `chat(messages, ...) -> str` signature
plus a `name` attribute.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

# Optional tomllib (Python 3.11+); fall back gracefully on older shapes
# even though pyproject.toml requires 3.11+.
try:
    import tomllib  # type: ignore[import-not-found]
except ImportError:
    tomllib = None  # type: ignore[assignment]


_ENV_KEY = {
    "gemini": "HV_GEMINI_KEY",
    "anthropic": "HV_ANTHROPIC_KEY",
    "openai": "HV_OPENAI_KEY",
    # OpenAI-compatible cloud providers (use their own base_urls)
    "openrouter": "HV_OPENROUTER_KEY",
    "groq": "HV_GROQ_KEY",
    "deepseek": "HV_DEEPSEEK_KEY",
    "together": "HV_TOGETHER_KEY",
    "mistral": "HV_MISTRAL_KEY",
    "perplexity": "HV_PERPLEXITY_KEY",
    # Local AI servers (no key needed by default but env may carry one
    # for password-protected setups)
    "ollama": "HV_OLLAMA_KEY",
    "lmstudio": "HV_LMSTUDIO_KEY",
    "llamafile": "HV_LLAMAFILE_KEY",
    # Custom OpenAI-compatible endpoint (user supplies base_url)
    "custom": "HV_CUSTOM_KEY",
}

# Cloud provider base URLs for OpenAI-compat clients
_BASE_URL = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "openai": None,  # default OpenAI endpoint
    "openrouter": "https://openrouter.ai/api/v1",
    "groq": "https://api.groq.com/openai/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "together": "https://api.together.xyz/v1",
    "mistral": "https://api.mistral.ai/v1",
    "perplexity": "https://api.perplexity.ai",
    # Local servers — defaults; user can override via HV_<NAME>_BASE_URL
    "ollama": "http://localhost:11434/v1",
    "lmstudio": "http://localhost:1234/v1",
    "llamafile": "http://localhost:8080/v1",
}

# Default model per provider (user can override via HV_<NAME>_MODEL)
_DEFAULT_MODEL = {
    "gemini": "gemini-2.5-flash",
    "openai": "gpt-4o-mini",
    "openrouter": "anthropic/claude-sonnet-4.5",
    "groq": "llama-3.3-70b-versatile",
    "deepseek": "deepseek-chat",
    "together": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    "mistral": "mistral-large-latest",
    "perplexity": "llama-3.1-sonar-small-128k-online",
    "ollama": "llama3.2",
    "lmstudio": "local-model",
    "llamafile": "local-model",
}

# Resolution priority — Anthropic Claude is the default. Huntova was
# built using Claude end-to-end, so we ship with the model that gave us
# the best agent quality during development. Other providers stay
# fully supported and the user can switch at any time via `huntova
# onboard` or `HV_AI_PROVIDER`.
_DEFAULT_ORDER = (
    "anthropic", "gemini", "openai", "ollama", "lmstudio", "llamafile",
    "openrouter", "groq", "deepseek", "together", "mistral", "perplexity",
    "custom",
)

# Local providers don't strictly need an API key (Ollama default has
# none; LM Studio same). The local-AI server is detected by trying to
# connect to its base URL.
_LOCAL_PROVIDERS = ("ollama", "lmstudio", "llamafile")


@runtime_checkable
class Provider(Protocol):
    name: str

    def chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        timeout_s: float = 30.0,
        response_format: dict[str, Any] | None = None,
    ) -> str: ...


# ── Settings + key resolution ───────────────────────────────────────


def _local_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "huntova" / "config.toml"


def _load_local_settings() -> dict[str, Any]:
    if not tomllib:
        return {}
    p = _local_config_path()
    if not p.exists():
        return {}
    try:
        with p.open("rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def _key_for(provider_name: str, settings: dict[str, Any]) -> str | None:
    env_var = _ENV_KEY.get(provider_name)
    if not env_var:
        return None
    # 1) user_settings dict (works in both cloud + local). Two acceptable
    # shapes: top-level "HV_GEMINI_KEY" or nested under "providers".
    val = settings.get(env_var)
    if not val:
        provs = settings.get("providers") or {}
        if isinstance(provs, dict):
            val = (provs.get(provider_name) or {}).get("api_key") if isinstance(provs.get(provider_name), dict) else provs.get(provider_name)
    if val:
        return str(val).strip() or None
    # 2) Local secrets store (keychain / Fernet file)
    try:
        from secrets_store import get_secret
        v = get_secret(env_var)
        if v:
            return v
    except Exception:
        pass
    # 3) Environment variable
    return os.environ.get(env_var) or None


def _resolve_settings(user_settings: dict[str, Any] | None) -> dict[str, Any]:
    if user_settings is not None:
        return user_settings
    app_mode = (os.environ.get("APP_MODE") or "cloud").strip().lower()
    if app_mode == "local":
        return _load_local_settings()
    return {}


# ── Provider implementations ────────────────────────────────────────


class _OpenAICompatibleProvider:
    """Shared logic for any OpenAI-compatible chat-completions endpoint
    (OpenAI itself + Gemini's OpenAI-compat surface)."""

    name: str = "openai-compatible"
    _default_model: str = "gpt-4o-mini"

    def __init__(self, api_key: str, base_url: str | None = None):
        if not api_key:
            raise RuntimeError(f"{self.name}: API key required")
        from openai import OpenAI
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = OpenAI(**kwargs)

    def chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        timeout_s: float = 30.0,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        kwargs: dict[str, Any] = {
            "model": model or self._default_model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "timeout": timeout_s,
        }
        if response_format:
            kwargs["response_format"] = response_format
        resp = self._client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""


class GeminiProvider(_OpenAICompatibleProvider):
    name = "gemini"
    _default_model = (os.environ.get("HV_GEMINI_MODEL") or "").strip() or "gemini-2.5-flash"

    def __init__(self, api_key: str):
        super().__init__(
            api_key=api_key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )


class OpenAIProvider(_OpenAICompatibleProvider):
    name = "openai"
    _default_model = (os.environ.get("HV_OPENAI_MODEL") or "").strip() or "gpt-4o-mini"

    def __init__(self, api_key: str):
        super().__init__(api_key=api_key, base_url=None)


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str):
        if not api_key:
            raise RuntimeError("anthropic: API key required")
        try:
            import anthropic  # type: ignore[import-not-found]
        except ImportError as e:
            raise RuntimeError(
                "anthropic SDK not installed. Run `pip install anthropic`."
            ) from e
        self._client = anthropic.Anthropic(api_key=api_key)

    def chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        timeout_s: float = 30.0,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        # Anthropic Messages API: system message lives outside the
        # messages array. Split it out so the SDK doesn't reject the
        # first message as a non-user role.
        system_msg = ""
        kept: list[dict[str, Any]] = []
        for m in messages:
            if (m.get("role") or "").lower() == "system":
                system_msg = (system_msg + "\n\n" + str(m.get("content") or "")).strip()
            else:
                kept.append(m)
        model_id = (model or (os.environ.get("HV_ANTHROPIC_MODEL") or "").strip()
                    or "claude-sonnet-4-5-20250929")
        # Stability fix (audit wave 26): Anthropic's Messages API has
        # no native `response_format={"type":"json_object"}` like
        # OpenAI does. CLAUDE.md and our docstring both promised the
        # prefill JSON-mode trick was implemented, but `response_format`
        # was silently dropped from the signature — every caller asking
        # for JSON (chat dispatcher, score validator, score breakdown)
        # got back free-form prose with markdown fences, then
        # downstream `json.loads` blew up. The trick: append an
        # assistant-role message ending with `{` so Claude continues
        # from there, producing JSON without any preamble. Prepend
        # `{` back to the output before returning.
        _wants_json = (isinstance(response_format, dict)
                       and (response_format.get("type") in ("json_object", "json")))
        if _wants_json:
            kept = list(kept) + [{"role": "assistant", "content": "{"}]
        kwargs: dict[str, Any] = {
            "model": model_id,
            "messages": kept,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "timeout": timeout_s,
        }
        if system_msg:
            kwargs["system"] = system_msg
        resp = self._client.messages.create(**kwargs)
        # response.content is a list of blocks; concatenate text blocks
        out: list[str] = []
        for block in getattr(resp, "content", []) or []:
            text = getattr(block, "text", None)
            if text:
                out.append(text)
        body = "".join(out)
        if _wants_json:
            # Prefill makes the model continue *after* the `{`; prepend
            # it back so callers receive a complete JSON string. If the
            # model overshoots the closing `}` (rare but possible when
            # max_tokens cuts mid-token), trim at the last `}` so
            # downstream json.loads still parses.
            body = "{" + body
            _last = body.rfind("}")
            if _last != -1:
                body = body[: _last + 1]
        return body


# ── Public API ──────────────────────────────────────────────────────


def list_available_providers(user_settings: dict[str, Any] | None = None) -> list[str]:
    """Names of providers that have a usable API key configured."""
    settings = _resolve_settings(user_settings)
    return [name for name in _DEFAULT_ORDER if _key_for(name, settings)]


# Provider override via contextvars — asyncio-safe and propagates
# through `asyncio.to_thread()` automatically (which a thread-local
# would NOT, because FastAPI's executor reuses worker threads across
# requests; a stale override from request A would visible to request B).
# A subagent (or any caller wanting per-call routing without rewriting
# every AI call site) sets the override and get_provider() honors it
# until the contextvars Context exits or push_provider_override(None)
# clears it. This is how the chat's AI selector + the multi-agent
# spawn fan-out pick per-agent providers.
import contextvars as _cv
import threading as _threading
_provider_override: _cv.ContextVar[str] = _cv.ContextVar("hv_provider_override", default="")
# Subagent threads aren't run under asyncio.to_thread (they're plain
# threading.Thread daemons), so they don't auto-inherit the calling
# coroutine's Context. For those we keep a per-thread fallback dict
# that spawn_subagent populates explicitly. ContextVar is checked
# first, then this fallback.
_subagent_thread_overrides: "dict[int, str]" = {}
_subagent_thread_lock = _threading.Lock()


def push_provider_override(slug: str | None) -> None:
    """Pin the current request/subagent context's get_provider() to a
    specific slug, or clear when slug is None/empty.

    For async request handlers (`/api/chat`) the override rides the
    contextvars Context for the duration of the request — Python's
    contextvars are per-asyncio-task, so two concurrent /api/chat
    requests on the same event-loop thread don't interfere.

    For subagent daemon threads (plain threading.Thread, not
    asyncio.to_thread) the calling Context isn't inherited, so we
    also write a per-thread-id entry that get_provider() reads as a
    fallback.

    Stability fix (audit wave 22): the per-thread write used to fire
    unconditionally, including from async handlers running on the
    event-loop thread. Because every async task on a given loop shares
    the same `threading.get_ident()`, request A's per-thread entry
    leaked into request B's get_provider() if B's contextvar wasn't
    explicitly set. Now the per-thread fallback only writes from real
    daemon threads (no running asyncio loop), which matches its
    original intent — covering threading.Thread spawns from
    spawn_subagent, not async handlers.
    """
    s = (slug or "").strip().lower()
    valid = s in _DEFAULT_ORDER
    _provider_override.set(s if valid else "")
    # Detect: are we running inside an asyncio event loop right now?
    # If yes, contextvars handle isolation cleanly and the per-thread
    # dict would just leak across tasks. Skip it.
    try:
        import asyncio as _asyncio
        _asyncio.get_running_loop()
        return
    except RuntimeError:
        pass  # no running loop → real thread, use the fallback
    tid = _threading.get_ident()
    with _subagent_thread_lock:
        if valid:
            _subagent_thread_overrides[tid] = s
        else:
            _subagent_thread_overrides.pop(tid, None)


def get_provider(user_settings: dict[str, Any] | None = None) -> Provider:
    """Return a configured provider instance.

    Raises RuntimeError if no provider has a usable API key. CLI
    callers should catch this and direct the user to `huntova onboard`.
    """
    settings = _resolve_settings(user_settings)
    # Context-var override wins over settings — this is what makes the
    # multi-agent spawn fan-out run on different providers without
    # anyone rewriting their settings on disk. Read via contextvars
    # first (set by api_chat under await), then fall back to the
    # per-thread dict (set by spawn_subagent for daemon threads that
    # don't share the calling Context).
    override = _provider_override.get("")
    if not override:
        with _subagent_thread_lock:
            override = _subagent_thread_overrides.get(_threading.get_ident(), "")
    if override and override in _DEFAULT_ORDER:
        key = _key_for(override, settings)
        if key:
            return _build(override, key, settings)
    preferred = (settings.get("preferred_provider") or "").strip().lower()

    # If a preference is set AND has a key, use it.
    # Stability fix (audit wave 26): local providers (ollama, lmstudio,
    # llamafile) intentionally don't require a key — `_build` accepts
    # `"no-key"` as a placeholder for them. The previous version only
    # honoured `preferred` when `_key_for(preferred, settings)` returned
    # a truthy value, which is None for unkeyed local providers, so
    # `preferred_provider="ollama"` was silently ignored and the resolver
    # fell through to the default priority order and picked Anthropic.
    # Treat local providers as keyless so an explicit preference wins.
    if preferred in _DEFAULT_ORDER:
        key = _key_for(preferred, settings)
        if key:
            return _build(preferred, key, settings)
        if preferred in _LOCAL_PROVIDERS:
            return _build(preferred, "no-key", settings)

    # Otherwise: first available in the default priority order.
    for name in _DEFAULT_ORDER:
        key = _key_for(name, settings)
        if key:
            return _build(name, key, settings)

    raise RuntimeError(
        "no API key configured — run `huntova onboard` to save one "
        "to your OS keychain, or set HV_ANTHROPIC_KEY (default), "
        "HV_OPENAI_KEY, or HV_GEMINI_KEY in your environment."
    )


def _build(name: str, key: str, settings: dict[str, Any] | None = None) -> Provider:
    settings = settings or {}
    if name == "gemini":
        return GeminiProvider(key)
    if name == "anthropic":
        return AnthropicProvider(key)
    if name == "openai":
        return OpenAIProvider(key)
    # All OpenAI-compatible cloud + local providers
    if name in _BASE_URL:
        return _GenericOpenAICompatProvider(
            name=name,
            api_key=key or "no-key",  # local providers may not need one
            # Stability fix (audit wave 30): _key_for() resolves
            # settings → keychain → env (settings wins). The previous
            # base_url + default_model lookups had env winning over
            # settings — so a user with both set ended up with the
            # ENV base_url paired with the SETTINGS key, contradicting
            # the documented priority. Match _key_for: settings first.
            base_url=(settings.get(f"HV_{name.upper()}_BASE_URL")
                      or os.environ.get(f"HV_{name.upper()}_BASE_URL")
                      or _BASE_URL.get(name)),
            default_model=(settings.get(f"HV_{name.upper()}_MODEL")
                           or os.environ.get(f"HV_{name.upper()}_MODEL")
                           or _DEFAULT_MODEL.get(name)
                           or "local-model"),
        )
    if name == "custom":
        # Custom endpoint: requires HV_CUSTOM_BASE_URL set in env /
        # settings. Settings wins per _key_for ordering (a237 fix).
        base_url = (settings.get("HV_CUSTOM_BASE_URL")
                    or os.environ.get("HV_CUSTOM_BASE_URL"))
        if not base_url:
            raise RuntimeError(
                "custom provider requires HV_CUSTOM_BASE_URL. Set it to "
                "your endpoint URL (e.g. https://my-api.example.com/v1)."
            )
        # Normalize: strip trailing slash, append /v1 if missing — most
        # OpenAI-compatible endpoints expect /v1, and the SDK silently
        # 404s otherwise. User-provided URL like "https://api.example.com"
        # → "https://api.example.com/v1". Already-correct URLs are
        # preserved.
        base_url = base_url.rstrip("/")
        if not (base_url.endswith("/v1") or "/v1/" in base_url
                or base_url.endswith("/api") or base_url.endswith("/openai")):
            base_url = base_url + "/v1"
        return _GenericOpenAICompatProvider(
            name="custom",
            api_key=key or "no-key",
            base_url=base_url,
            default_model=(settings.get("HV_CUSTOM_MODEL")
                           or os.environ.get("HV_CUSTOM_MODEL")
                           or "custom-model"),
        )
    raise ValueError(f"Unknown provider: {name}")


class _GenericOpenAICompatProvider(_OpenAICompatibleProvider):
    """Any OpenAI-compatible endpoint — Ollama, LM Studio, OpenRouter,
    Groq, DeepSeek, Together, Mistral, Perplexity, or a user-specified
    custom endpoint."""

    def __init__(self, name: str, api_key: str, base_url: str | None,
                 default_model: str):
        self.name = name
        self._default_model = default_model
        # _OpenAICompatibleProvider.__init__ enforces api_key; pass a
        # placeholder for keyless local servers.
        super().__init__(api_key=api_key or "no-key", base_url=base_url)


def detect_local_servers() -> dict[str, dict]:
    """Probe localhost ports for known local AI servers (Ollama, LM
    Studio, llamafile). Used by `huntova onboard` to auto-suggest
    detected local options before falling back to cloud.

    Returns a dict {provider_name: {available: bool, model_count: int,
    base_url: str}} for each local provider.
    """
    import urllib.request
    import json as _json
    out: dict[str, dict] = {}
    probes = {
        "ollama":   ("http://localhost:11434/api/tags", "models"),
        "lmstudio": ("http://localhost:1234/v1/models", "data"),
        "llamafile":("http://localhost:8080/v1/models", "data"),
    }
    for name, (url, key) in probes.items():
        info = {"available": False, "model_count": 0, "base_url": _BASE_URL[name]}
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=1.5) as r:
                if 200 <= r.status < 300:
                    body = _json.loads(r.read().decode("utf-8", errors="ignore") or "{}")
                    items = body.get(key) if isinstance(body, dict) else None
                    info["available"] = True
                    info["model_count"] = len(items) if isinstance(items, list) else 0
        except Exception:
            pass
        out[name] = info
    return out


# ── OpenAI-compat response shim ─────────────────────────────────────
# Drop-in replacement for `client.chat.completions.create(**kwargs)`.
# Returns an object whose `.choices[0].message.content` matches the
# OpenAI SDK shape so existing callers in app.py / server.py don't
# need to change beyond the function name.


class _CompatMessage:
    __slots__ = ("content", "role")

    def __init__(self, content: str, role: str = "assistant"):
        self.content = content
        self.role = role


class _CompatChoice:
    __slots__ = ("message", "index", "finish_reason")

    def __init__(self, content: str):
        self.message = _CompatMessage(content)
        self.index = 0
        self.finish_reason = "stop"


class _CompatResponse:
    __slots__ = ("choices", "model", "id", "usage")

    def __init__(self, content: str, model: str = ""):
        self.choices = [_CompatChoice(content)]
        self.model = model
        self.id = ""
        self.usage = None


def chat_compat(
    messages: list[dict[str, Any]] | None = None,
    model: str | None = None,
    max_tokens: int = 2048,
    temperature: float = 0.7,
    timeout: float | None = None,
    response_format: dict[str, Any] | None = None,
    user_settings: dict[str, Any] | None = None,
    **_unused: Any,
) -> _CompatResponse:
    """Drop-in for `client.chat.completions.create(**kwargs)`.

    Resolves the user's provider via get_provider(user_settings),
    runs the chat, returns an OpenAI-shaped response so call sites
    that read `resp.choices[0].message.content` keep working without
    edits.
    """
    p = get_provider(user_settings)
    text = p.chat(
        messages=messages or [],
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout_s=float(timeout) if timeout is not None else 30.0,
        response_format=response_format,
    )
    return _CompatResponse(content=text or "", model=model or "")
