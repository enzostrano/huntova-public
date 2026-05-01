# Huntova plugin registry

This is the JSON manifest that `huntova plugins search` queries to
list community plugins. To list a plugin:

1. Fork the [huntova-plugins repo](https://github.com/enzostrano/huntova-plugins).
2. Add a JSON object to `registry.json` with these keys:
   - `name` (required) — the PyPI package name
   - `description` (required) — one-line summary
   - `author` (required) — your handle or org
   - `install` (required) — full install command (`pip install ...`)
   - `homepage` (recommended) — repo URL
   - `hooks` (required) — list of plugin lifecycle hooks the plugin implements
   - `version` (required) — current version
   - `license` (recommended) — e.g. `MIT`, `Apache-2.0`
3. Open a PR. Maintainer review checks: package exists on PyPI,
   plugin loads cleanly in a fresh venv, no obvious malicious code.

Plugins NOT in the registry can still be installed manually
(`pip install <name>`) — the registry is just for discovery.
