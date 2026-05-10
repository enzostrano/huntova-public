# GitHub Actions workflows (drafts)

These YAMLs were drafted during the BYOK pivot but couldn't be pushed
from the local Git client (the OAuth app lacks the `workflow` scope).

To add them to the repo:

1. Open the GitHub web UI → Settings → Actions → Workflows → New workflow.
2. Click **Set up a workflow yourself**.
3. Paste the contents of `publish.yml` (or `smoke.yml`).
4. Commit to master.

## `publish.yml` — Publish to PyPI on tag

Triggered on `v*` tags or via workflow_dispatch. Builds sdist + wheel
with `python -m build`, validates metadata with `twine check`, and
publishes via PyPI Trusted Publishing (OIDC — no API token in the
repo).

To use: tag `v0.1.0a2` and push. The workflow handles the rest.

You'll also need to:
- Create a Trusted Publisher entry on PyPI: <https://pypi.org/manage/account/publishing/>
- Owner: `enzostrano` · Repo: `huntova` · Workflow: `publish.yml` · Environment: `pypi`

## `smoke.yml` — Smoke test on every PR

Runs `tools/smoke_test_local.py` on every push/PR to master. Also
parse-checks every Python module and validates `static/install.sh`
syntax. Doesn't need any secrets.

Recommended to require this check before merging via Settings →
Branches → Branch protection rules.
