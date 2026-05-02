# Writing plugins for Huntova

Plugins are the customisation surface. They hook into specific points
in the agent lifecycle, do one focused thing, and disclose what
capabilities they need so users can audit them before installing.

## Where plugins live

Two discovery paths:

1. **User plugins** at `~/.config/huntova/plugins/*.py`. Loaded on every
   hunt. Scaffold one with `huntova plugins create <name>`.
2. **Pip packages** declaring an `huntova.plugins` entry point in their
   `pyproject.toml`. Installed via `huntova plugins install <name>` from
   the registry, or directly with `pip install` then re-running the agent.

## Hook points

| Hook            | Fired                                          | Receives                  | Returns         |
|-----------------|------------------------------------------------|---------------------------|-----------------|
| `pre_search`    | Before SearXNG query batch is sent             | `(ctx, queries)`          | `queries`       |
| `post_search`   | After raw results return, before scoring       | `(ctx, results)`          | `results`       |
| `pre_score`     | Before AI scoring of a single page             | `(ctx, lead)`             | `lead`          |
| `post_score`    | After AI scoring, before qualification         | `(ctx, lead, score)`      | `(lead, score)` |
| `post_qualify`  | After deep-qualify pass on a high-fit lead     | `(ctx, lead)`             | `lead`          |
| `post_save`     | After lead is written to local DB              | `(ctx, lead)`             | None (side-effects only) |
| `pre_draft`     | Before email/LinkedIn draft is generated       | `(ctx, lead, draft)`      | `draft`         |
| `post_draft`    | After draft is generated                       | `(ctx, lead, draft)`      | `draft`         |

(Updated a304 — corrected `pre_score` argument from `page_text` to `lead`,
`pre_draft` from `lead` to `lead+draft`, and added the explicit Returns
column. Source-of-truth signatures live in `plugins.py:11-18`.)

`ctx` is a `UserAgentContext` with the user's settings, AI provider, and
event bus. Plugins can read user state but should not mutate it.

## Capability disclosure

Plugins must declare their capabilities so the user knows what they can
do. The four classes of capability:

- `network` — opens outbound HTTP / TCP / WebSocket connections.
- `secrets` — reads or writes the OS keychain / `secrets.enc`.
- `filesystem_write` — writes files outside the per-hunt log dir.
- `subprocess` — spawns a child process.

The `huntova plugins` browser shows a coloured pill per capability so
users can spot a "writes filesystem + spawns subprocess" plugin before
installing it.

## Minimal plugin

```python
# ~/.config/huntova/plugins/slack_ping.py
class SlackPing:
    name = "slack-ping"
    version = "1.0.0"
    capabilities = ["network"]

    def post_save(self, ctx, lead):
        import urllib.request, json, os
        url = os.environ.get("HV_SLACK_WEBHOOK_URL")
        if not url:
            return
        body = json.dumps({"text": f"new lead: {lead.get('org_name')}"}).encode()
        urllib.request.urlopen(url, data=body, timeout=5)
```

That's it. Drop the file and the next hunt picks it up automatically.

## Pip-package plugin

```toml
# pyproject.toml in your package
[project.entry-points."huntova.plugins"]
my-crm-sink = "my_crm_sink:Plugin"
```

Then `pip install my-crm-sink` and Huntova will discover and load it on
the next hunt.

## Testing

```bash
huntova plugins                     # confirm yours is discovered
huntova plugins ls --format json    # see the full descriptor
huntova hunt --max-leads 1 --verbose # confirm it's firing
```

For unit tests:

```python
from plugins import get_registry, reset_for_tests
reset_for_tests()
reg = get_registry()
reg.discover()
assert "slack-ping" in [p["name"] for p in reg.list_plugins()]
```

## Submitting to the registry

The community registry at `docs/plugin-registry/registry.json` is what
populates `huntova plugins search` and the public `/plugins` browse
page. To add yours:

1. Open a PR adding your entry to the registry JSON.
2. Include `name`, `description`, `hooks`, `capabilities`, `version`,
   `author`, `homepage`, and `install` (the pip package name).
3. Plugins start as `verified=false` (community badge). Maintainers
   move plugins to `verified=true` after a basic security review.

## Invariants

The agent runs your plugin synchronously inside the hunt loop. To stay
fast and safe:

- **No long-running calls.** Anything > 2s should be queued or async.
  Slow plugins block the whole hunt.
- **No exceptions across hooks.** A plugin that raises in `post_score`
  is logged and skipped for that hook only. Don't rely on it firing.
- **No mutation of `ctx`.** Read-only. Plugins that need state should
  hold their own.
- **Idempotency.** Hooks fire once per event but a hunt can be re-run.
  Design for that (e.g. don't double-write the same lead to your CRM).

## Existing plugins (bundled)

Nine reference plugins ship in the wheel and load on every hunt.
Disable all of them with `HV_DISABLE_BUNDLED_PLUGINS=1`.

| Plugin             | Hook         | Capabilities      |
|--------------------|--------------|-------------------|
| `csv-sink`         | `post_save`  | `filesystem_write` |
| `dedup-by-domain`  | `post_search` | (none)           |
| `slack-ping`       | `post_save`  | `network`         |
| `discord-ping`     | `post_save`  | `network`         |
| `telegram-ping`    | `post_save`  | `network`         |
| `whatsapp-ping`    | `post_save`  | `network`         |
| `generic-webhook`  | `post_save`  | `network`         |
| `recipe-adapter`   | `pre_search` | (none)            |
| `adaptation-rules` | `post_score` | (none)            |

Read their source in `bundled_plugins.py` for canonical examples.
