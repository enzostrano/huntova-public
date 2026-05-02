"""
Huntova plugin protocol.

Huntova's moat is a community plugin ecosystem, not the core agent loop.
The runtime stays small; users + third parties extend the lead-gen
pipeline by registering hook implementations.

Lifecycle hooks (chain-of-custody — each receives mutable state and
returns the new state for the next plugin in the chain):

    pre_search(ctx, queries) -> queries
    post_search(ctx, results) -> results
    pre_score(ctx, lead) -> lead
    post_score(ctx, lead, score) -> (lead, score)
    post_qualify(ctx, lead) -> lead
    post_save(ctx, lead) -> None        # fire-and-forget side effects
    pre_draft(ctx, lead, draft) -> draft
    post_draft(ctx, lead, draft) -> draft

Discovery:
    1. Published packages: `entry_points(group="huntova.plugins")`
    2. User scripts:       `~/.config/huntova/plugins/*.py`

Both are loaded on startup. Plugin authors set `priority` on hook
methods for ordering; default is 50.

Example:
    from plugins import Plugin, HookContext

    class WappalyzerPlugin:
        name = "wappalyzer"
        version = "0.1.0"
        def post_qualify(self, ctx: HookContext, lead: dict) -> dict:
            lead["tech_stack"] = detect_stack(lead.get("org_website"))
            return lead

A hooks-list-empty no-op runtime is the default — plugins.run() is
safe to call even when no plugins are registered.
"""
from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol, runtime_checkable


# ── Hook context shared with every plugin ───────────────────────────


@dataclass
class HookContext:
    """Minimal context passed to plugins. Plugins should NOT touch
    global state — everything they need lives here."""
    settings: dict[str, Any] = field(default_factory=dict)
    provider_name: str = ""
    user_id: int | None = None
    secrets: dict[str, str] = field(default_factory=dict, repr=False)
    meta: dict[str, Any] = field(default_factory=dict)


# ── Plugin Protocol ─────────────────────────────────────────────────


@runtime_checkable
class Plugin(Protocol):
    name: str
    version: str

    # Optional capability declaration — surfaces in the registry and
    # `huntova plugins list` so users see WHAT the plugin can do
    # before installing. Per Tab 2 (round 69): not a sandbox, just
    # honest disclosure. Standard values: "network" (HTTP), "secrets"
    # (reads from secrets_store), "filesystem_write" (writes outside
    # ~/.local/share/huntova), "subprocess" (spawns child processes).
    capabilities: list[str]

    # All hooks are optional — Plugin Protocol lists them so type
    # checkers can verify implementations, but PluginRegistry only
    # registers methods that exist on the instance.

    def pre_search(self, ctx: HookContext, queries: list[str]) -> list[str]:
        ...

    def post_search(self, ctx: HookContext, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ...

    def pre_score(self, ctx: HookContext, lead: dict[str, Any]) -> dict[str, Any]:
        ...

    def post_score(self, ctx: HookContext, lead: dict[str, Any], score: float) -> tuple[dict[str, Any], float]:
        ...

    def post_qualify(self, ctx: HookContext, lead: dict[str, Any]) -> dict[str, Any]:
        ...

    def post_save(self, ctx: HookContext, lead: dict[str, Any]) -> None:
        ...

    def pre_draft(self, ctx: HookContext, lead: dict[str, Any], draft: str) -> str:
        ...

    def post_draft(self, ctx: HookContext, lead: dict[str, Any], draft: str) -> str:
        ...


# ── Registry ────────────────────────────────────────────────────────


_HOOK_NAMES = (
    "pre_search", "post_search",
    "pre_score", "post_score",
    "post_qualify", "post_save",
    "pre_draft", "post_draft",
)


class PluginRegistry:
    """Discovers, loads, and dispatches plugins by hook name."""

    def __init__(self) -> None:
        self._plugins: list[object] = []
        self._hooks: dict[str, list[tuple[int, Callable[..., Any], object]]] = {}
        self._load_errors: list[tuple[str, str]] = []  # (name, error message)

    # ── Public API ────────────────────────────────────────────────

    def discover(self) -> list[str]:
        """Find + load all plugins. Idempotent — calling twice is a
        no-op for plugins already registered (compared by class name).

        Discovery order: bundled reference plugins first (so user
        plugins can override them by class-name shadowing), then
        published packages via entry_points, then local user scripts.
        """
        loaded: list[str] = []
        # Bundled reference plugins (csv-sink / dedup-by-domain / slack-ping)
        try:
            from bundled_plugins import register_bundled
            loaded.extend(register_bundled(self))
        except Exception as e:
            self._load_errors.append(("(bundled)", f"{type(e).__name__}: {e}"))
        loaded.extend(self._load_entry_points())
        loaded.extend(self._load_local_scripts())
        return loaded

    def register(self, plugin: object) -> None:
        """Register a plugin instance. Methods named after hook lifecycle
        get added to the dispatch chain at their declared `priority`
        (default 50)."""
        # a275: dedupe by plugin `name` not by Python class name. Pre-a275
        # this used `type(plugin).__name__` which meant: (1) two distinct
        # plugins both happening to call their class `Plugin` (a very
        # common boilerplate name) silently shadowed each other; (2) two
        # plugins both declaring `name = "csv-sink"` from different
        # classes both registered, running their hook chain twice. The
        # `name` attribute is the documented identity per the Plugin
        # protocol — dedupe should match.
        plugin_name = getattr(plugin, "name", "") or type(plugin).__name__
        existing_names = {getattr(p, "name", "") or type(p).__name__ for p in self._plugins}
        if plugin_name in existing_names:
            return
        self._plugins.append(plugin)
        import asyncio as _asyncio
        for hook in _HOOK_NAMES:
            fn = getattr(plugin, hook, None)
            if not callable(fn):
                continue
            # Async hooks would return a coroutine object that the
            # synchronous run() chain would treat as the carry value,
            # silently corrupting downstream plugins.
            if _asyncio.iscoroutinefunction(fn):
                self._load_errors.append((
                    getattr(plugin, "name", type(plugin).__name__),
                    f"{hook}: async hooks not supported — make this method synchronous"))
                continue
            prio = int(getattr(fn, "priority", 50))
            self._hooks.setdefault(hook, []).append((prio, fn, plugin))
            self._hooks[hook].sort(key=lambda t: t[0])

    def run(self, hook: str, ctx: HookContext, *args: Any) -> Any:
        """Execute the hook chain. Each plugin's return value replaces
        the carry value passed to the next plugin. For a void hook
        (post_save), all callables fire and the original args are
        returned untouched.

        Returns the final carry value (for non-void hooks) or None.
        Plugin exceptions are caught + logged so one buggy plugin
        can't break the whole pipeline.
        """
        chain = self._hooks.get(hook, [])
        if not chain:
            return args[0] if len(args) == 1 else args
        carry: Any = args[0] if len(args) == 1 else args
        # Two-argument hooks: pass (lead, score) or (lead, draft) unpacked.
        # Single-argument hooks: pass carry as a single positional arg.
        # Each plugin invoked EXACTLY ONCE per call to run() — earlier
        # versions called fn() twice for post_score/pre_draft/post_draft
        # which doubled adaptation deltas.
        is_two_arg = hook in ("post_score", "pre_draft", "post_draft")
        # Stability fix (audit wave 29): track the current lead
        # separately for two-arg hooks. Previously, when a post_score
        # plugin returned just a scalar score (vs (lead, score) tuple),
        # the next plugin in the chain was called with `args[0]` — the
        # ORIGINAL un-mutated lead — losing any mutations earlier
        # plugins applied via tuple returns. Same drop applied to
        # pre_draft / post_draft when carry was a string. Hold the
        # latest lead in `current_lead` and feed it forward instead.
        current_lead: Any = args[0] if len(args) >= 1 else None
        for _prio, fn, plugin in chain:
            try:
                if hook == "post_save":
                    # Fire-and-forget: return value ignored.
                    fn(ctx, *args)
                    continue
                if is_two_arg:
                    # Hook signatures with two positional args after ctx:
                    #   post_score(ctx, lead, score) -> (lead, score)
                    #   pre_draft(ctx, lead, draft)  -> draft
                    #   post_draft(ctx, lead, draft) -> draft
                    if isinstance(carry, tuple) and len(carry) >= 2:
                        # Tuple form: refresh current_lead from the
                        # tuple, then forward both elements.
                        if isinstance(carry[0], dict):
                            current_lead = carry[0]
                        result = fn(ctx, current_lead, carry[1])
                    else:
                        # carry was reduced to a single value by an earlier
                        # plugin in the chain — re-pair with the latest
                        # lead so chain-of-custody mutations survive.
                        second = args[1] if len(args) >= 2 else None
                        if hook == "post_score":
                            if isinstance(carry, (int, float)):
                                result = fn(ctx, current_lead, float(carry))
                            elif isinstance(carry, dict):
                                # Plugin returned just a lead dict, no
                                # new score — keep the previous score.
                                current_lead = carry
                                result = fn(ctx, current_lead,
                                            float(second) if second is not None else 0.0)
                            else:
                                result = fn(ctx, current_lead,
                                            float(second) if second is not None else 0.0)
                        else:
                            # pre_draft / post_draft return a string; carry
                            # is the current draft value.
                            result = fn(ctx, current_lead,
                                        carry if isinstance(carry, str) else (second or ""))
                else:
                    # Single-argument hook (pre_search, post_search,
                    # pre_score, post_qualify): pass carry as one arg.
                    result = fn(ctx, carry)
                if result is not None:
                    carry = result
            except Exception as e:
                # Don't crash the agent — log + continue.
                plugin_name = getattr(plugin, "name", type(plugin).__name__)
                self._load_errors.append((plugin_name, f"{hook}: {type(e).__name__}: {e}"))
        return carry

    def list_plugins(self) -> list[dict[str, Any]]:
        """Return summary info for `huntova plugins` and the doctor command."""
        out: list[dict[str, Any]] = []
        for p in self._plugins:
            hooks_implemented = [h for h in _HOOK_NAMES if callable(getattr(p, h, None))]
            caps = getattr(p, "capabilities", None)
            if not isinstance(caps, list):
                caps = []
            out.append({
                "name": getattr(p, "name", type(p).__name__),
                "version": getattr(p, "version", "?"),
                "hooks": hooks_implemented,
                "capabilities": caps,
                "class": f"{type(p).__module__}.{type(p).__name__}",
            })
        return out

    def errors(self) -> list[tuple[str, str]]:
        return list(self._load_errors)

    # ── Discovery internals ───────────────────────────────────────

    def _load_entry_points(self) -> list[str]:
        loaded: list[str] = []
        try:
            from importlib.metadata import entry_points
            try:
                eps = entry_points(group="huntova.plugins")  # py3.10+
            except TypeError:
                eps = entry_points().get("huntova.plugins", [])  # py3.9 fallback
        except Exception as e:
            self._load_errors.append(("(entry_points)", str(e)))
            return loaded
        for ep in eps:
            try:
                cls = ep.load()
                instance = cls() if isinstance(cls, type) else cls
                self.register(instance)
                loaded.append(getattr(instance, "name", ep.name))
            except Exception as e:
                self._load_errors.append((ep.name, f"{type(e).__name__}: {e}"))
        return loaded

    def _local_plugin_dir(self) -> Path:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        return Path(base) / "huntova" / "plugins"

    def _load_local_scripts(self) -> list[str]:
        loaded: list[str] = []
        plug_dir = self._local_plugin_dir()
        if not plug_dir.exists():
            return loaded
        for py_file in sorted(plug_dir.glob("*.py")):
            try:
                spec = importlib.util.spec_from_file_location(
                    f"huntova_plugin_{py_file.stem}", py_file
                )
                if not spec or not spec.loader:
                    continue
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)  # type: ignore[union-attr]
                # a275: only consider symbols defined IN this module — was
                # iterating `dir(mod)` which returns transitively-imported
                # third-party classes too (e.g. `requests.Session`,
                # `OpenAI`, ORM bases). The old loop instantiated each one
                # with `obj()` to test for hooks, producing noisy load-
                # errors and unbounded side effects (every imported lib's
                # constructor fired). Now we filter by `obj.__module__ ==
                # mod.__name__` so only plugin-author-defined classes are
                # candidates.
                _mod_name = getattr(mod, "__name__", "")
                for attr in dir(mod):
                    if attr.startswith("_"):
                        continue
                    obj = getattr(mod, attr)
                    if isinstance(obj, type):
                        # Skip classes imported from other modules.
                        if getattr(obj, "__module__", None) != _mod_name:
                            continue
                        # Class — instantiate
                        try:
                            inst = obj()
                        except Exception as e:
                            self._load_errors.append((py_file.name, f"instantiate {attr}: {e}"))
                            continue
                    else:
                        # Module-level instance: must have been defined here.
                        if getattr(type(obj), "__module__", None) != _mod_name:
                            continue
                        inst = obj
                    if not hasattr(inst, "name"):
                        continue
                    if not any(callable(getattr(inst, h, None)) for h in _HOOK_NAMES):
                        continue
                    # Per-plugin try/except so one failed register() doesn't
                    # silently abort the remaining plugins in the same file.
                    # Pre-a49 the outer try block at line 281 swallowed register
                    # errors and broke the for-attr loop, dropping plugins B,C,...
                    # whenever plugin A in the same file failed.
                    try:
                        self.register(inst)
                        loaded.append(getattr(inst, "name", py_file.stem))
                    except Exception as _re:
                        self._load_errors.append(
                            (py_file.name, f"register {getattr(inst,'name',attr)}: {type(_re).__name__}: {_re}")
                        )
            except Exception as e:
                self._load_errors.append((py_file.name, f"{type(e).__name__}: {e}"))
        return loaded


# ── Module-level singleton ──────────────────────────────────────────


_registry: PluginRegistry | None = None


def get_registry() -> PluginRegistry:
    """Lazy-init the global registry. Call discover() once early in
    startup (server.py on_startup or cli before agent run)."""
    global _registry
    if _registry is None:
        _registry = PluginRegistry()
    return _registry


def reset_for_tests() -> None:
    global _registry
    _registry = None
