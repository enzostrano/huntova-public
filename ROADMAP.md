# Huntova Roadmap

> Living document. Updated each release. Pulled from the per-release
> "Known bugs" sections of `RELEASE-v*.md` and from in-flight audit
> findings.

## v0.1.0 final (target: post-May-5 launch)

Bugs to clear before tagging stable:

- [x] **Hunt timeout fires only at iteration boundaries.** Closed
      in v0.1.0a7 — three `_check_budget()` probes added in app.py.
- [ ] **`recipe-adapter` / `adaptation-rules` plugins fire only on
      CLI `huntova recipe run`.** The DNA-prompt feedback wiring
      (v0.1.0a4) is the primary smart-loop now; this is secondary.
      Migrate to plugin-context-passed adaptation dict.
- [x] **Top-level `huntova --help` 30-subcommand dump.** Closed in
      v0.1.0a9 — categorized into Getting started / Daily use /
      Outreach / Plugins / Daemon ops / Utility.
- [ ] **Settings UI doesn't yet persist `theme: light`** — toggle
      writes to `/api/settings` but the dashboard CSS doesn't have
      a complete light-mode palette ready.
- [ ] **Keychain sentinel auto-clear** — comment promises self-
      healing in `secrets_store.get_secret` that's not implemented.
      Either wire it up or change the sentinel to time-bounded
      (7-day TTL).

## v0.2.0 (mid-May)

- [ ] **`huntova daemon` Windows support.** Currently launchd (macOS)
      and systemd `--user` (Linux) only. Add Task Scheduler XML for
      Windows.
- [ ] **Apollo / Clay direct API import** (not just CSV) via
      `huntova migrate from-apollo --api-key …`.
- [ ] **Live LinkedIn enrichment** as an opt-in plugin (not bundled —
      requires the user's own Sales Navigator session cookie).
- [ ] **Per-hunt resource budget UI** — already shipped budget caps;
      next round adds a live progress meter showing time/cost burn
      so users can stop a hunt before it overshoots.
- [ ] **Multi-language support for query generation** — currently
      English-biased. Add German / French / Spanish prompt templates.

## v0.3.0 (June)

- [ ] **Plugin sandbox.** Today's plugin model trusts user-installed
      Python files. Move to a capability-disclosed worker process
      with strict subprocess isolation (mirrors OpenClaw's sandbox
      pattern).
- [ ] **Native macOS app shell** (Tauri or PyApp) — for users who
      don't want to install Python first. Just a `.dmg` that ships
      Python + Huntova bundled.
- [ ] **Per-user agent DNA versioning.** History of past DNA
      generations + diff view ("you trained me to avoid X — was that
      right?").

## v1.0 (target: end of summer)

- [ ] **PyPI publish.** Currently install via
      `pipx install git+https://...`. Once the API is stable, ship
      to PyPI as `pipx install huntova`.
- [ ] **Signed releases.** GitHub release artifacts with SLSA
      provenance + sigstore signatures.
- [ ] **Public PGP key** for security disclosures.
- [ ] **Localised /landing pages** (DE, FR, ES, IT) — high-intent
      SEO for the European agency market.

## Done (recent releases — most recent first)

See per-release notes:
- [`RELEASE-v0.1.0a9.md`](./RELEASE-v0.1.0a9.md) — round-8 audit
  fixes + `huntova benchmark` + grouped `--help`
- [`RELEASE-v0.1.0a8.md`](./RELEASE-v0.1.0a8.md) — round-7 audit
  fixes + `huntova logs` + onboard cheat sheet
- [`RELEASE-v0.1.0a7.md`](./RELEASE-v0.1.0a7.md) — hunt timeout
  closed + `huntova recipe export/import` + 4 round-6 fixes
- [`RELEASE-v0.1.0a6.md`](./RELEASE-v0.1.0a6.md) — silent-failure
  killers + `huntova approve` + 3 cosmetic fixes
- [`RELEASE-v0.1.0a5.md`](./RELEASE-v0.1.0a5.md) — security
  hardening + `huntova migrate` + first-run polish
- [`RELEASE-v0.1.0a4.md`](./RELEASE-v0.1.0a4.md) — adaptive
  smart-loop + 8 launch-blocker fixes
- [`RELEASE-v0.1.0a3.md`](./RELEASE-v0.1.0a3.md) — single-command
  install + chat REPL + Anthropic default

## How items get added here

A new entry lands here when:
1. A multi-agent audit flags it but the fix is non-trivial enough
   to defer past the current release boundary.
2. The user (Enzo) calls it out as future work in chat.
3. An external bug report / PR comes in and we triage it as
   "next-release scope".

If you want to suggest a roadmap addition, open an issue on
`enzostrano/huntova-public` with the label `roadmap`.
