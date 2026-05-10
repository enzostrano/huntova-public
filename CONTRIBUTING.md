# Contributing to Huntova

Huntova is a small project. The bar for accepting changes is high, but
the path is simple: open an issue first if you're shipping anything
non-trivial, then send a focused PR.

## Reporting bugs

Run `huntova doctor` and paste the output into the issue. It redacts
secrets automatically. For UI / web-wizard bugs, include browser +
version + a screenshot.

If a hunt produced unexpected results, attach the output of:

```bash
huntova hunt --explain-scores --max-leads 5 --json > hunt.jsonl
```

(strip lead names + emails before posting if they're real prospects).

## Suggesting features

Open an issue first. The product surface is intentionally small; the
default answer to "could we add X?" is "open a plugin". Plugins hook
into eight points in the agent lifecycle (`pre_search`, `post_search`,
`pre_score`, `post_score`, `post_qualify`, `post_save`, `pre_draft`,
`post_draft`) and ship as pip packages. See `docs/PLUGINS.md` for the
full plugin API.

## Setting up a dev checkout

```bash
git clone https://github.com/enzostrano/huntova-public.git
cd huntova-public
python3.13 -m venv venv
source venv/bin/activate
pip install -e .[anthropic,browser]
playwright install chromium     # optional but recommended
huntova onboard                 # sets up provider + keychain
```

Pre-flight before sending a PR:

```bash
python -c "import ast; \
  [ast.parse(open(f).read()) for f in \
   ('cli.py','server.py','providers.py','db.py','plugins.py')]"
huntova doctor                  # all configured probes pass
huntova test-integrations       # exit code 0 on a vanilla install
```

## PR checklist

- [ ] One topic per PR. Refactors and bug-fixes go in separate PRs.
- [ ] Title is one short imperative sentence ("fix: race in onboard
      browser launch", not "fixed onboard").
- [ ] If the change touches the agent's scoring or query generation,
      include a before/after `--explain-scores` excerpt in the description.
- [ ] If it adds a new env var, document it in `docs/CONFIG.md`.
- [ ] If it changes a public CLI command, update `README.md` and the
      `--help` text.

## Plugin contributions

The fastest way to ship something useful is to write a plugin. The
community registry at `docs/plugin-registry/registry.json` is curated
— open a PR adding your entry and the maintainers will review it.

Plugins must:

- Declare their capabilities (`network`, `secrets`, `filesystem_write`,
  `subprocess`) so users can audit before installing.
- Be importable as a single Python file or a published pip package.
- Pass `huntova plugins install <yours>` end-to-end on a fresh venv.

## Code style

- Python 3.11+. Type hints encouraged but not required.
- No new dependencies without a strong justification — small wheel size
  is the install-experience differentiator.
- Keep CLI output terse on success, helpful on failure. The first 60
  seconds of a new user's experience is the priority.

## Licence

By submitting a PR you agree your contribution is licensed under the
project's AGPL-3.0-or-later terms.
