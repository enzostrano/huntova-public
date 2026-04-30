# Huntova v0.1.0a25 — 2026-05-01

CLAUDE.md fully rewritten for the local-first reality. Future Claude
sessions opening this repo will now see accurate guidance instead of
the legacy SaaS spec.

## Updates

### CLAUDE.md rewrite
- Was: PostgreSQL-only, Stripe-required, Render deployment, Gemini
  hardcoded, "credits per lead" central to the model.
- Now: local-first BYOK CLI with cloud as a parallel mode. Documents:
  - `runtime.py` capability flags (the gating source of truth)
  - Frontend gating classes (`hv-billing-only` / `hv-saas-only` /
    `hv-auth-only` / `hv-local-only`)
  - Provider abstraction (`providers.py` resolution order)
  - Anthropic Claude as the BYOK default
  - SQLite-or-Postgres `db_driver.py` shim
  - Orphan-commit release pipeline (the actual workflow we use)
  - Every public release ships a `RELEASE-vNN.md` + uploads
    `install.sh`
- Added a "Rules That Must Never Be Broken" section reflecting the
  new defaults — including: never reorder providers to drop Anthropic
  as default, never add SaaS copy without gating it.
- Parse-check examples switched from a JS-eval pattern to `node
  --check static/app.js` for cleaner, hook-friendly verification.

## Bug fixes
- None — pure docs work.

## Known issues
- Same as a24 (mobile sidebar drawer TODO, etc.).
