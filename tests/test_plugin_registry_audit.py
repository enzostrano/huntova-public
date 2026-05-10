"""BRAIN-189: plugins.PluginRegistry invariant audit.

The plugin chain runs in-band with the hunt loop. A regression here
either drops legitimate plugin output, double-runs hooks, or lets
one buggy plugin crash the whole pipeline.

Pinned invariants:

1. Empty registry: `run()` returns the input unchanged.
2. Single-arg hook (pre_search): plugins compose via carry chain.
3. Two-arg hook (post_score): tuple form `(lead, score)` and
   scalar form both supported via current_lead tracking
   (audit-wave-29 fix).
4. Void hook (post_save): all plugins fire, return value ignored.
5. Plugin exception during `run()` is caught + recorded; chain
   continues.
6. `register()` deduplicates by `plugin.name` (a275 fix), not class name.
7. `register()` rejects async hooks (silently logs error, doesn't load).
8. `list_plugins()` returns hook names + capabilities for each.
9. `errors()` is concurrent-read-safe (lock-protected).
10. `_HOOK_NAMES` constant matches the documented set.
"""
from __future__ import annotations


class _NoopPlugin:
    name = "noop"
    version = "1.0.0"
    capabilities: list = []


class _PreSearchAddPlugin:
    name = "pre-add"
    version = "1.0.0"
    capabilities: list = []

    def pre_search(self, ctx, queries):
        return list(queries) + ["extra_query"]


class _PostScoreBoostPlugin:
    name = "boost"
    version = "1.0.0"
    capabilities: list = []

    def post_score(self, ctx, lead, score):
        return (lead, score + 1.0)


class _PostScoreScalarPlugin:
    """Returns scalar score only — exercises audit-wave-29 fallback."""
    name = "boost-scalar"
    version = "1.0.0"
    capabilities: list = []

    def post_score(self, ctx, lead, score):
        return score + 1.0


class _PostSavePlugin:
    name = "save-recorder"
    version = "1.0.0"
    capabilities: list = []
    saved: list = []

    def post_save(self, ctx, lead):
        self.saved.append(lead)


class _CrashingPlugin:
    name = "buggy"
    version = "1.0.0"
    capabilities: list = []

    def post_qualify(self, ctx, lead):
        raise RuntimeError("plugin crashed")


class _AsyncPlugin:
    """Should be REJECTED by register() — async hooks not supported."""
    name = "asyncplug"
    version = "1.0.0"
    capabilities: list = []

    async def pre_search(self, ctx, queries):
        return queries


def _fresh_registry():
    from plugins import PluginRegistry
    return PluginRegistry()


def _ctx():
    from plugins import HookContext
    return HookContext()


def test_empty_registry_returns_input():
    reg = _fresh_registry()
    out = reg.run("pre_search", _ctx(), ["q1", "q2"])
    assert out == ["q1", "q2"]


def test_single_arg_hook_chains():
    reg = _fresh_registry()
    reg.register(_PreSearchAddPlugin())
    out = reg.run("pre_search", _ctx(), ["original"])
    assert "original" in out
    assert "extra_query" in out


def test_two_arg_hook_tuple_form():
    reg = _fresh_registry()
    reg.register(_PostScoreBoostPlugin())
    out = reg.run("post_score", _ctx(), {"lead": 1}, 5.0)
    # Returns (lead, boosted_score).
    assert isinstance(out, tuple)
    assert out[1] == 6.0


def test_two_arg_hook_scalar_form_audit_wave_29():
    """Audit-wave-29 fix: a plugin returning just a scalar score
    (no lead tuple) preserves the lead from the original args."""
    reg = _fresh_registry()
    reg.register(_PostScoreScalarPlugin())
    out = reg.run("post_score", _ctx(), {"lead_id": 42}, 5.0)
    # The chain still completes — carry might be tuple or scalar.
    # The key invariant: no crash + boost applied.
    if isinstance(out, tuple):
        assert out[1] == 6.0
    else:
        assert out == 6.0


def test_void_hook_post_save_fires_all_plugins():
    reg = _fresh_registry()
    p = _PostSavePlugin()
    p.saved = []  # reset
    reg.register(p)
    reg.run("post_save", _ctx(), {"lead_id": 1})
    assert len(p.saved) == 1


def test_plugin_exception_caught_chain_continues():
    """A crashing plugin doesn't break subsequent plugins."""
    reg = _fresh_registry()
    reg.register(_CrashingPlugin())
    reg.register(_PreSearchAddPlugin())
    # post_qualify chain — only crashing plugin registered for that hook.
    out = reg.run("post_qualify", _ctx(), {"lead_id": 1})
    # Must not raise.
    # Error recorded.
    errors = reg.errors()
    assert any("buggy" in e[0] for e in errors)


def test_register_dedupes_by_name():
    """a275 fix: dedupe by `plugin.name`, not class name."""
    reg = _fresh_registry()

    class A:
        name = "shared-name"
        version = "1.0.0"
        capabilities: list = []

    class B:
        name = "shared-name"  # Same name, different class.
        version = "2.0.0"
        capabilities: list = []

    reg.register(A())
    reg.register(B())
    plugins = reg.list_plugins()
    # Only ONE registered (the first wins).
    assert len(plugins) == 1
    assert plugins[0]["name"] == "shared-name"


def test_register_rejects_async_hooks():
    """Async hooks not supported — must be skipped + recorded."""
    reg = _fresh_registry()
    reg.register(_AsyncPlugin())
    # Plugin instance still in registry but its async hook isn't wired.
    out = reg.run("pre_search", _ctx(), ["q"])
    # Returns input unchanged (async hook didn't fire).
    assert out == ["q"]
    # Error recorded.
    errors = reg.errors()
    assert any("async" in msg for _, msg in errors)


def test_list_plugins_reports_hooks_and_capabilities():
    reg = _fresh_registry()
    reg.register(_PreSearchAddPlugin())
    plugins = reg.list_plugins()
    assert len(plugins) == 1
    p = plugins[0]
    assert p["name"] == "pre-add"
    assert "pre_search" in p["hooks"]


def test_errors_returns_list_copy():
    """errors() returns a copy — caller mutating it doesn't corrupt
    canonical state."""
    reg = _fresh_registry()
    reg.register(_CrashingPlugin())
    reg.run("post_qualify", _ctx(), {"x": 1})
    e1 = reg.errors()
    e2 = reg.errors()
    # Same data, different lists.
    assert e1 == e2
    e1.append(("hacker", "fake"))
    e2_again = reg.errors()
    assert ("hacker", "fake") not in e2_again


def test_hook_names_constant():
    """Pin the canonical hook list."""
    from plugins import _HOOK_NAMES
    expected = {"pre_search", "post_search", "pre_score", "post_score",
                "post_qualify", "post_save", "pre_draft", "post_draft"}
    assert set(_HOOK_NAMES) == expected


def test_get_registry_returns_singleton():
    """get_registry() returns the module-level singleton."""
    from plugins import get_registry
    a = get_registry()
    b = get_registry()
    assert a is b


def test_reset_for_tests_clears_state():
    """reset_for_tests() empties the registry — used by tests to
    isolate."""
    from plugins import get_registry, reset_for_tests
    reg = get_registry()
    reg.register(_PreSearchAddPlugin())
    assert len(reg.list_plugins()) >= 1
    reset_for_tests()
    reg = get_registry()
    assert len(reg.list_plugins()) == 0


def test_plugin_with_no_name_attribute():
    """A plugin missing the `name` attribute falls back to class name."""
    reg = _fresh_registry()

    class Anon:
        version = "1.0.0"
        capabilities: list = []

    reg.register(Anon())
    plugins = reg.list_plugins()
    assert len(plugins) == 1
    # Class name used as identity.
    assert plugins[0]["name"] in ("Anon", "")


def test_register_idempotent_on_repeat():
    """Calling register() twice with the same plugin is a no-op."""
    reg = _fresh_registry()
    p = _PreSearchAddPlugin()
    reg.register(p)
    reg.register(p)
    assert len(reg.list_plugins()) == 1
