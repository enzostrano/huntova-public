"""Plugin Protocol + PluginRegistry — discovery, dispatch, error isolation."""
from __future__ import annotations


def _make_plugin_file(plug_dir, body: str, name: str = "demo.py"):
    plug_dir.mkdir(parents=True, exist_ok=True)
    p = plug_dir / name
    p.write_text(body)
    return p


def test_registry_discovers_local_script(local_env, monkeypatch):
    """A *.py file in ~/.config/huntova/plugins gets registered."""
    monkeypatch.setenv("HV_DISABLE_BUNDLED_PLUGINS", "1")
    plug_dir = local_env["config_dir"] / "huntova" / "plugins"
    _make_plugin_file(plug_dir, """
class DemoPlugin:
    name = "demo"
    version = "0.1.0"
    def post_qualify(self, ctx, lead):
        lead["touched_by_demo"] = True
        return lead
""")
    from plugins import PluginRegistry, HookContext
    reg = PluginRegistry()
    loaded = reg.discover()
    assert "demo" in loaded
    names = [p["name"] for p in reg.list_plugins()]
    assert "demo" in names


def test_registry_dispatch_chains_results(local_env):
    """Two plugins, both implementing pre_search — chain in priority order."""
    from plugins import PluginRegistry, HookContext

    class Adder:
        name = "adder"
        version = "0.0.1"
        def pre_search(self, ctx, queries):
            return queries + ["from-adder"]

    class Prepender:
        name = "prepender"
        version = "0.0.1"
        def pre_search(self, ctx, queries):
            return ["from-prepender"] + queries

    reg = PluginRegistry()
    reg.register(Adder())
    reg.register(Prepender())
    out = reg.run("pre_search", HookContext(), ["original"])
    assert "from-adder" in out
    assert "from-prepender" in out
    assert "original" in out


def test_registry_isolates_buggy_plugin(local_env):
    """A plugin that raises shouldn't crash the chain."""
    from plugins import PluginRegistry, HookContext

    class GoodPlugin:
        name = "good"
        version = "0.0.1"
        def post_qualify(self, ctx, lead):
            lead["good"] = True
            return lead

    class BadPlugin:
        name = "bad"
        version = "0.0.1"
        def post_qualify(self, ctx, lead):
            raise RuntimeError("intentional failure")

    reg = PluginRegistry()
    reg.register(BadPlugin())
    reg.register(GoodPlugin())
    result = reg.run("post_qualify", HookContext(), {"org_name": "X"})
    # Good plugin still ran
    assert result.get("good") is True
    # Bad plugin's error was captured
    errs = reg.errors()
    assert any("bad" in n for n, _ in errs)


def test_registry_post_save_is_void(local_env):
    """post_save fires-and-forgets — return value is ignored."""
    from plugins import PluginRegistry, HookContext

    seen: list[dict] = []

    class Sink:
        name = "sink"
        version = "0.0.1"
        def post_save(self, ctx, lead):
            seen.append(lead)
            return "ignored-return-value"

    reg = PluginRegistry()
    reg.register(Sink())
    reg.run("post_save", HookContext(), {"org_name": "Aurora"})
    assert len(seen) == 1
    assert seen[0]["org_name"] == "Aurora"


def test_registry_no_plugins_is_noop(local_env):
    """run() on a hook with zero plugins returns the original carry value."""
    from plugins import PluginRegistry, HookContext
    reg = PluginRegistry()
    out = reg.run("pre_search", HookContext(), ["x"])
    assert out == ["x"]


def test_registry_register_idempotent(local_env):
    """Same plugin class registered twice is only counted once."""
    from plugins import PluginRegistry

    class P:
        name = "x"
        version = "0.0.1"
        def pre_search(self, ctx, queries):
            return queries

    reg = PluginRegistry()
    reg.register(P())
    reg.register(P())
    assert len(reg.list_plugins()) == 1


def test_bundled_plugins_register_by_default(local_env):
    """csv-sink, dedup-by-domain, slack-ping all show up after discover()."""
    from plugins import PluginRegistry
    reg = PluginRegistry()
    loaded = reg.discover()
    names = [p["name"] for p in reg.list_plugins()]
    for expected in ("csv-sink", "dedup-by-domain", "slack-ping"):
        assert expected in names, f"missing bundled plugin: {expected}"
    # Each should have come from bundled_plugins module
    for p in reg.list_plugins():
        if p["name"] in ("csv-sink", "dedup-by-domain", "slack-ping"):
            assert "bundled_plugins" in p["class"]


def test_bundled_plugins_disable_env(local_env, monkeypatch):
    """HV_DISABLE_BUNDLED_PLUGINS=1 skips registration of the bundled set."""
    monkeypatch.setenv("HV_DISABLE_BUNDLED_PLUGINS", "1")
    from plugins import PluginRegistry
    reg = PluginRegistry()
    reg.discover()
    names = [p["name"] for p in reg.list_plugins()]
    assert "csv-sink" not in names
    assert "dedup-by-domain" not in names
    assert "slack-ping" not in names


def test_csv_sink_no_op_without_config(local_env, tmp_path):
    """csv-sink writes nothing when no path is configured."""
    from bundled_plugins import CSVSinkPlugin
    from plugins import HookContext
    p = CSVSinkPlugin()
    ctx = HookContext()
    # No HV_CSV_SINK_PATH, no [csv_sink] in settings — should silently no-op.
    p.post_save(ctx, {"org_name": "Test", "lead_id": "L1"})
    # Nothing else to assert beyond "didn't raise"


def test_csv_sink_appends_when_path_configured(local_env, tmp_path):
    """csv-sink writes a header on first row + the row itself."""
    from bundled_plugins import CSVSinkPlugin
    from plugins import HookContext
    out = tmp_path / "out.csv"
    p = CSVSinkPlugin()
    ctx = HookContext(settings={"csv_sink": {"path": str(out)}})
    p.post_save(ctx, {"org_name": "Aurora", "lead_id": "L1", "fit_score": 9})
    text = out.read_text()
    assert "lead_id,org_name" in text  # header row
    assert "Aurora" in text
    p.post_save(ctx, {"org_name": "Tessera", "lead_id": "L2", "fit_score": 8})
    text2 = out.read_text()
    # Header still appears once
    assert text2.count("lead_id,org_name") == 1
    assert "Tessera" in text2


def test_dedup_filters_repeat_domains(local_env, tmp_path, monkeypatch):
    """dedup-by-domain drops a result if its domain was seen recently."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    from bundled_plugins import DedupByDomainPlugin
    from plugins import HookContext
    p = DedupByDomainPlugin()
    ctx = HookContext()
    results = [
        {"url": "https://foo.com/page1", "title": "A"},
        {"url": "https://bar.com/", "title": "B"},
        {"url": "https://foo.com/page2", "title": "C"},  # same domain
    ]
    out = p.post_search(ctx, results)
    domains = sorted({r["title"] for r in out})
    # foo.com appears once (first entry); bar.com once. C is dropped.
    assert "A" in domains
    assert "B" in domains
    assert "C" not in domains


def test_slack_ping_no_op_without_webhook(local_env):
    """slack-ping is silent when no webhook URL is configured."""
    from bundled_plugins import SlackPingPlugin
    from plugins import HookContext
    p = SlackPingPlugin()
    ctx = HookContext()
    # No webhook configured — should silently no-op.
    p.post_save(ctx, {"org_name": "Test"})


def test_recipe_adapter_no_op_without_env(local_env, monkeypatch):
    """RecipeAdaptationPlugin returns queries unchanged when env is unset."""
    monkeypatch.delenv("HV_RECIPE_ADAPTATION", raising=False)
    from bundled_plugins import RecipeAdaptationPlugin
    from plugins import HookContext
    p = RecipeAdaptationPlugin()
    qs = ["q1", "q2", "q3"]
    out = p.pre_search(HookContext(), qs)
    assert out == qs


def test_recipe_adapter_suppresses_terms(local_env, monkeypatch):
    """suppress_terms drops matching queries case-insensitively."""
    import json as _json
    monkeypatch.setenv("HV_RECIPE_ADAPTATION", _json.dumps({
        "winning_terms": [], "suppress_terms": ["agency"], "added_queries": []
    }))
    from bundled_plugins import RecipeAdaptationPlugin
    from plugins import HookContext
    p = RecipeAdaptationPlugin()
    out = p.pre_search(HookContext(), ["video producer hire", "marketing agency hiring", "studio team"])
    assert "marketing agency hiring" not in out
    assert "video producer hire" in out
    assert "studio team" in out


def test_recipe_adapter_boosts_winning_terms(local_env, monkeypatch):
    """winning_terms move matching queries to the front."""
    import json as _json
    monkeypatch.setenv("HV_RECIPE_ADAPTATION", _json.dumps({
        "winning_terms": ["founder"], "suppress_terms": [], "added_queries": []
    }))
    from bundled_plugins import RecipeAdaptationPlugin
    from plugins import HookContext
    p = RecipeAdaptationPlugin()
    out = p.pre_search(HookContext(), ["q1 generic", "founder-led shop", "another"])
    assert out[0] == "founder-led shop"


def test_recipe_adapter_prepends_added_queries(local_env, monkeypatch):
    """added_queries land at the very front."""
    import json as _json
    monkeypatch.setenv("HV_RECIPE_ADAPTATION", _json.dumps({
        "winning_terms": [], "suppress_terms": [],
        "added_queries": ["new query A", "new query B"]
    }))
    from bundled_plugins import RecipeAdaptationPlugin
    from plugins import HookContext
    p = RecipeAdaptationPlugin()
    out = p.pre_search(HookContext(), ["original q1", "original q2"])
    assert out[0] == "new query A"
    assert out[1] == "new query B"
    assert "original q1" in out
