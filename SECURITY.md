# Security policy

## Reporting a vulnerability

Use GitHub's **private vulnerability reporting** at <https://github.com/enzostrano/huntova-public/security/advisories/new> with:
- A description of the vulnerability and its potential impact
- Reproduction steps (or a proof-of-concept)
- The version of Huntova affected (`huntova version`)

Please do **not** open a public GitHub issue for security vulnerabilities. We'll
respond within 72 hours and aim to publish a patched release within 7 days for
critical issues.

## Threat model

Huntova is a **local-first CLI**. The threat model is built on three principles:

1. **Your AI key never leaves your machine.** It's stored in your OS keychain
   (preferred), or in `~/.config/huntova/secrets.enc` encrypted with a Fernet
   key derived from machine identifiers, or as a permission-locked plaintext
   file at `~/.config/huntova/secrets.json`. The CLI sends the key directly
   to your provider (Gemini / Anthropic / OpenAI) — Huntova has no proxy or
   middle-tier that ever sees it.

2. **Your leads stay on your machine.** They're stored in
   `~/.local/share/huntova/db.sqlite`. Sharing via `huntova share` mints a
   public `/h/<slug>` URL that resolves only against the local server you
   control — there is no central Huntova-hosted database of your data.

3. **Plugins are not sandboxed.** The plugin system loads Python files from
   `~/.config/huntova/plugins/*.py` and executes them with full process
   permissions. A malicious plugin can read your secrets, exfiltrate your
   leads, or run arbitrary code. **Treat plugins like any other Python
   package**: only install ones you trust.

   The official registry (`huntova plugins search`) marks a small set of
   plugins as `verified ✓` — these have been reviewed by the core team.
   Community-listed plugins are marked `community ○` — install at your
   own risk. Each plugin declares its `capabilities` (network / secrets /
   filesystem_write / subprocess) so you can see what it can do before
   installing.

## What's NOT in the threat model

- **Network egress filtering.** The CLI runs on your machine and can reach
  any host. If you need network isolation, run it inside a container or
  firewalled network of your own.
- **Encrypted-at-rest SQLite.** The SQLite file is stored unencrypted by
  default. Use full-disk encryption (FileVault / BitLocker / LUKS) if you
  need disk-level protection.
- **Hardened plugin sandbox.** As above — plugins run with full Python
  access. Don't install a plugin you haven't read.

## Known limitations

- The CLI's local FastAPI server binds to `127.0.0.1` by default, so it's
  only accessible from your machine. If you bind to `0.0.0.0` for any reason
  (NOT recommended), be aware that the server has no authentication in
  single-user mode.
- The `/api/runtime` endpoint is intentionally public (it returns capability
  flags so the dashboard can render correctly). It does not expose any user
  data.

## Disclosure schedule

- **Critical** (key exfiltration, RCE): 7 days from confirmation to patched
  release.
- **High** (lead data exposure): 14 days.
- **Medium** / **Low**: next minor release.

## Past advisories

(Caught by internal multi-agent code review during launch prep, not by
external researchers. Listed for transparency.)

| Version | Issue | Severity |
|---------|-------|----------|
| v0.1.0a5 | SSRF via `/api/webhooks/test` accepted private/loopback URLs | High |
| v0.1.0a5 | `/api/smtp/test` port-scan oracle (differentiated error responses) | Medium |
| v0.1.0a5 | `/api/settings` GET could echo legacy-stored secrets | Medium |
| v0.1.0a5 | Test endpoints rate-unlimited (credential-stuffing relay) | Medium |
| v0.1.0a4 | `huntova chat` crashed on Anthropic provider (multi-turn) | Low |
| v0.1.0a3 | Admin token compared with `==` (timing-attack vulnerable) | Medium |
| v0.1.0a3 | systemd `Environment=` not escaped (silent daemon fail) | Medium |
| v0.1.0a3 | OG-SVG slug not validated before DB lookup | Low |
| v0.1.0a3 | `/api/setup/key` fail-open on runtime import error | Medium |

We'll credit reporters in the changelog unless they prefer otherwise.
