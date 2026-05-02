# Changelog

All releases are tagged on [enzostrano/huntova-public](https://github.com/enzostrano/huntova-public/releases). This file is the durable, human-readable summary; per-release detail (a single `RELEASE-v<version>.md` written transiently for the GitHub release body) is not committed.

Versioning: `0.1.0aNN` alpha increments. Public install path: `pipx install huntova` or `curl -fsSL https://github.com/enzostrano/huntova-public/releases/latest/download/install.sh | sh`.

---

## 0.1.0a332 — May 2 2026 — Provider greyout refreshes after key-save + on team-edit modal open

### Bug fixes

- **Provider dropdown greyout was stale until page reload after saving a new API key** (`templates/jarvis.html:5104-5145`). User wired up a key in Settings → Engine, saw "✓ saved", flipped to chat to use it — the dock dropdown still showed "— not set up" on the just-configured provider until the user manually reloaded. Now: every successful `/api/setup/key` save calls `_loadProviderConfig()` + reapplies greyout to both selectors. Same fix on team-edit modal `openTeamEdit` so opening the modal re-syncs to the latest config.

---

## 0.1.0a331 — May 2 2026 — CRITICAL: Brain wizard "Complete training" no longer crashes + API-key-first guard + greyed-out unconfigured providers

### Critical bug fixes

- **CRASH FIX: `/api/wizard/complete` was throwing 500 Internal Server Error on EVERY call** (`app.py:5114-5300`). User reported: "when the wizard ends and you let me finish it says internal error and crashes." Reproduced live — server log: `AttributeError: 'str' object has no attribute 'get'` at `app.py:5277` inside `_generate_training_dossier`. Root cause: `examples_good` / `examples_bad` were being read as `wiz.get("example_good_clients", [])` — which the brain wizard sends as a comma-separated string ("Acme Beauty, Beta Skincare") OR a list of plain strings — but the consumer was iterating with `g.get("name", "")` assuming list-of-dicts. Every wizard completion crashed; same again at line 5191 in `anti_icp` learning. Added `_normalize_examples_top()` helper that handles all three shapes (string / list-of-strings / list-of-dicts) and runs at the top of the function so every downstream consumer sees the canonical list-of-dicts shape. Wizard now completes end-to-end (verified live: `train_count`, `archetype`, `dossier_version` all return 200 OK).

### New / UX

- **API-key-first guard on the Brain wizard** (`templates/jarvis.html:3641-3680`). Walking through 9 wizard questions with NO provider configured was wasted effort — the final `/api/wizard/complete` calls the user's AI and would throw. Now: when the Brain view loads with zero configured providers, render a clear amber-bordered card explaining the user needs to add an AI key first, with a one-click "🔑 Open Settings → API keys" CTA that switches view directly to the Engine tab. Skipped if any of the 13 supported providers (incl. local Ollama / LM Studio / llamafile, which need no key) is detected as configured.

- **Provider dropdowns now grey out unconfigured providers** (`templates/jarvis.html:1640-1690`). Both the chat-dock Engine selector AND the team-edit Provider override now show "— not set up" suffix on options without a configured key, with the option `disabled` so they can't be picked. If the currently-selected option becomes unconfigured (e.g. user deleted the key), the select reverts to "Auto" / "(use default)". Idempotent — re-applying after settings save is safe. User no longer has to remember which of the 13 providers they've actually wired up.

---

## 0.1.0a330 — May 2 2026 — Brain wizard surfaces specific `vague_issues` from server validation rejection

### Bug fixes

- **Brain wizard "Complete training" rejection now shows WHICH answer is too vague** (`templates/jarvis.html:4407-4438`). When `/api/wizard/complete` rejects with the validation gate, the server returns both a generic `error` string AND a `vague_issues` array naming the specific failing fields ("Business description is too vague", "Pick at least one decision-maker role", "'Global' is too broad — pick your top 2-3 strongest regions", etc.). The client was discarding `vague_issues` and only showing the generic error — users saw "Your answers need more detail" with no idea which of the 9 answers needed work. Now: renders the specific issue list as a bulleted block under the error so the user knows exactly what to fix.

---

## 0.1.0a329 — May 2 2026 — DATA LOSS FIX: email subject + body autosave (was throwing away every keystroke on view-switch)

### Bug fixes

- **Email subject + body edits in the lead-detail panel now autosave** (`templates/jarvis.html:3223-3289`). Previously edits ONLY persisted on explicit "💾 Save draft" click — typing a long email and then clicking "Back to leads" / sidebar nav threw away every keystroke with zero warning. Real data loss for users iterating on cold-email copy.

  Now: 1.5s debounced `/api/update` POST on every `input` event, plus an immediate flush on `blur`, plus immediate flush on the explicit Save button. The status pill shows "Saving…" → "✓ Saved" so the user has visible confirmation. Failed autosaves keep the user's text in the textarea (the toast says "your draft is still in the textarea") so accidental browser-tab-close after a network blip doesn't lose work either.

  Single-flight: a second input event during an in-flight save is silently dropped — the next input event after the in-flight save returns will trigger another save. So no thrash even when the user types fast.

---

## 0.1.0a328 — May 2 2026 — Pipeline kanban bulk-select checkboxes + brain wizard textarea-list dedupe

### Bug fixes

- **Pipeline kanban cards now have bulk-select checkboxes** (`templates/jarvis.html:2842-2872`). Switching from list view to pipeline view hid the bulk-select Set with no indication — users could be in pipeline view, click Apply on the bulk bar, and hit invisible-pre-selected leads. Now: each kanban card has a checkbox top-left that reads from / writes to the same shared `_bulkSelected` Set as the list view, so cross-view selection works as expected. `stopPropagation` on click + Space/Enter so the row's open-detail handler doesn't fire when toggling the checkbox.

- **Brain wizard `textarea-list` parsing now dedupes case-insensitively** (`templates/jarvis.html:4205-4231`). Was: the services field passed every line through `.trim() | filter(Boolean)` but allowed duplicates — common when users paste from a doc and have variant capitalisation ("Shopify migration" + "shopify migration"). Now: order-preserving dedupe via a `Set<lowercase>` so the wizard payload + AI prompts stay clean.

---

## 0.1.0a327 — May 2 2026 — Replace 6 OS `alert()` modals with in-page toasts + surface conversation-delete failures

### Bug fixes

- **6 `alert()` calls replaced with toast notifications** (`templates/jarvis.html:545-590` CSS + `:1640-1687` JS helper + 6 call sites). The OS-level `alert()` modal looked unprofessional, blocked the entire UI, and clipped on mobile. Replaced with a `_toast(message, kind)` helper that renders a self-dismissing notification in the bottom-right (color-coded info / ok / warn / err, auto-dismisses 4s for normal, 6s for errors). Affected sites: image upload too large, max attachments, upload failed (×2), bulk delete failed (×2).

- **Conversation delete now surfaces failures** (`templates/jarvis.html:1763-1793`). The handler was wrapping the entire fetch in `try { … } catch (_) {}` AND trusting the response without an `r.ok` check — so HTTP 4xx/5xx and network errors both silently no-op'd and the convo stayed in the sidebar with no user feedback. Now: explicit ok check + toast on either failure path. Re-clicking after a transient failure does the right thing.

---

## 0.1.0a326 — May 2 2026 — Big hunt round: brain auto-heal, fresh lead detail, lead-row a11y, kanban touch hover

### Bug fixes

- **`/api/wizard/status` now auto-heals stale top-level fields from `_wizard_answers`** (`server.py:7782-7820`). Pre-a322 the save-progress mapping used the wrong field names, so users could end up with `_wizard_answers.company_name = "SicilyCast"` while top-level `company_name = "Acme"` (the previous run's value). The trained-summary card showed the stale name forever. Now: every wizard status fetch checks for drift between the answers blob and the top-level fields and copies the answers values over when they differ. Idempotent — once they match, the heal is a no-op. Existing-install users no longer need to do a full retrain to recover from the pre-a322 mapping bug.

- **Lead detail panel always re-fetches from the server on open** (`templates/jarvis.html:2380-2400`). Previously trusted the cached `_leads` array; during an active hunt a lead's `fit_score`, `email_status`, contact info, etc. could change between list-load and detail-open and the user was looking at minutes-stale data. Now: every `openLeadDetail` does a fresh `/api/leads` call before rendering. Cheap (single-table SELECT, single-user mode) and the user is already paying view-switch latency.

- **Lead rows now keyboard-accessible** (`templates/jarvis.html:2701-2718` + kanban cards `:2752-2766`). The `<div class="lead-row">` rows were click-only — no `tabindex`, no role, no keyboard handler. Screen-reader and keyboard-only users could see leads in the list but had no way to open any of them. Added `tabindex="0"` + `role="button"` + `aria-label` + Enter/Space activation. Same fix on pipeline kanban cards. Excludes the row's checkbox from triggering the row open (its own Space toggle works as expected).

- **Pipeline kanban cards no longer JS-hover-only** (`templates/jarvis.html:2741-2770` markup + `:545-571` CSS). Was using `mouseenter`/`mouseleave` listeners with inline styles, which never fire on touch devices — phones and tablets saw the cards as completely static even when tapped. Replaced with a real `.hv-kanban-card` class so `:hover` and `:focus-visible` work everywhere. Adds a proper focus ring for keyboard users that matches the cyan accent.

### Hostinger marketing site

- **Pushed `templates/landing.html` (a325 self-healing version) to `darkred-barracuda-643789.hostingersite.com`** via Hostinger MCP `hosting_deployStaticWebsite`. Verified live: page now serves `<span data-hv-version>v0.1.0a324</span>` baseline + the auto-update script tail. Every visitor's browser hits the GitHub Releases API on load and rewrites the version to whatever the actual latest tag is — Hostinger never needs to be re-deployed for future releases.

---

## 0.1.0a325 — May 2 2026 — Live version sync everywhere: landing self-heals via GitHub API, dashboard sidebar refreshes on restart/visibility/poll

### Bug fixes

- **Landing page (`templates/landing.html`) was hardcoded to v0.1.0a184** — three places: hero badge, terminal demo block, "latest release" trust cell. 140 alpha releases stale. The Python server already re-substituted via `_read_landing_with_version()` when `/landing` is served from this app, but the static export at `darkred-barracuda-643789.hostingersite.com` froze on whatever shipped at deploy time. Bumped the baseline to a324 AND added a self-healing JS snippet at the end of landing.html that fetches `https://api.github.com/repos/enzostrano/huntova-public/releases/latest` on page load and rewrites every `[data-hv-version]` element to the actual latest tag (cached in localStorage 6h, soft-fails silent on rate-limit). Means the static Hostinger deploy now self-corrects forever — no redeploys needed when shipping new releases.

- **Dashboard sidebar version display only loaded once at page mount** (`templates/jarvis.html:5466-5479`). After a manual `pipx upgrade huntova` + `huntova serve` restart in another terminal, the open browser tab kept showing the old version until the user manually reloaded — looked like the upgrade hadn't applied. Now: `loadRuntime()` polls every 5 min, refreshes on `visibilitychange` (when user tabs back to the dashboard), and runs immediately after the in-browser auto-restart flow lands.

### Why

User reported: "you're not updating Hostinger site release number etc as well as GitHub stuff" + "also the app itself doesn't change the number". Both pinned the dashboard / marketing site to whatever version was hardcoded at deploy time and required manual re-deploys to refresh. Now both surfaces auto-track the actual latest release.

---

## 0.1.0a324 — May 2 2026 — One-click in-browser update flow (Install now → restart → release notes modal)

### New

- **One-click "Install now" button on the update banner** runs the entire upgrade without leaving the browser (`update_runner.py`, `server.py:8615-8722`, `templates/jarvis.html:881-905, 944-1063, 1750-1971`):
  - Click → modal pops up with a live terminal log streaming `pipx upgrade huntova` output line-by-line.
  - When the upgrade succeeds, two buttons appear: **Restart server now** or **Restart later** (apply on next manual restart).
  - "Restart now" hits `/api/update/restart` which schedules an `os.execv()` 1s out — server replaces its own process with a fresh interpreter loading the upgraded code, on the same port. The browser polls `/api/runtime` until the version flips, then renders a release-notes modal with the CHANGELOG section for the new version (markdown → DOM, no innerHTML).
  - Refuses to start an upgrade or restart while a hunt is running (would lose in-memory agent state) — surfaces the message inline in the modal.

- **Periodic 30-min update re-check** so users with the dashboard open for hours get notified when a release lands without having to refresh the tab.

- **`/api/update/release-notes?version=…` endpoint** that reads the matching `## 0.1.0aNN — …` section from `CHANGELOG.md` and returns it as markdown for the post-upgrade modal.

### Why

User reported the existing static banner felt thin: "currently i'm using the app, you pushed an update and i don't see a popup that says new update live, it should be like that for a better experience, and a button that updates and shuts the browser window and the opened terminal pushes the update and opens back up huntova and pop up says release logs / what's fixed etc." This release lands the full pro-grade flow: live notification (via 30-min poll) → one-click install → server self-restart → release notes modal — without the user touching the terminal.

### Implementation notes

- Upgrade subprocess is single-flight per process; a second click while one's in flight returns the running job id.
- Spawn uses list-form `subprocess.Popen` (no shell, no string interpolation) — safe by construction.
- Restart timeout is 60s; if the new server hasn't come up in that window the modal surfaces "Restart timed out, please reload manually" rather than spinning forever.
- Release-notes markdown renderer is a tiny subset (h2/h3, ul, **bold**, `code`, links). Output built as DOM nodes, never via `innerHTML`, so a malformed CHANGELOG can't inject HTML.

---

## 0.1.0a323 — May 2 2026 — SECURITY: SQLite DB + WAL/SHM sidecars now chmod 0o600 on every connection init

### Security fix

- **`~/.local/share/huntova/db.sqlite` was world-readable on most installs** (`db_driver.py:162-204`). The chmod-to-0600 only ran during `huntova init` / `huntova onboard` — users who skipped onboarding and went straight to `huntova serve` (the documented quick-start path) had the DB at the default umask (0644 on macOS). That meant any other OS user account on the same machine could read every lead, ICP answer, chat message, and session cookie via plain `cat`. Same for the `-wal` + `-shm` SQLite sidecars, which had never been chmod'd at all.

  Now: the SQLite driver's `__init__` chmods the main file + `-wal` + `-shm` + `-journal` to 0600 on every connection init (idempotent + cheap, ~1ms total), and tightens the parent directory `~/.local/share/huntova/` to 0700 so even file enumeration is blocked. Skipped on Windows (POSIX modes don't apply).

  Verified live: `-rw-r--r--` → `-rw-------` on all three files, parent dir → `drwx------` on first server boot.

### Why this matters

On personal laptops with one OS account, the leak is dormant. On any shared dev machine, multi-user system, or cloud VM (Codespaces, Coder, etc.), this was a real privacy break — leads + scraped contact info + ICP positioning + the user's emails to prospects were all readable by other accounts on the box. Heals on next `pipx upgrade huntova` + `huntova serve` boot for every existing install.

---

## 0.1.0a322 — May 2 2026 — Brain persistence: progressive saves now update top-level fields + Re-train flips server state

### Bug fixes

- **`/api/wizard/save-progress` was mapping STALE legacy field names** (`server.py:7388-7424`). The brain wizard sends real field names — `company_name`, `business_description`, `target_clients`, `services`, `buyer_roles`, `regions`, `example_good_clients`, `exclusions`, `outreach_tone`, `company_website` — but save-progress was only mapping the long-deprecated AI-interview shape (`business_name`, `what_you_do`, `ideal_customer`, …). Result: progressive saves persisted to `_wizard_answers` but never updated the top-level fields used by `/api/wizard/status` → trained-summary card showed the previous run's company name even after the user retrained with new answers. Symptoms: "brain forgotten on every run", "shows wrong company name", trained summary showing stale data forever. Mapped the real names through every save so top-level fields always reflect the latest in-progress state.

- **Re-train button only reset client-side state** (`templates/jarvis.html:3071-3105`). The server still reported `_interview_complete=true`, so reloading the page mid-retrain snapped the user back to the (now-stale) trained summary card — looked like persistence was broken. New `/api/wizard/start-retrain` endpoint flips `_interview_complete=false` and resets `_wizard_phase=0` while keeping every other top-level field intact (so the agent can still run hunts on the existing ICP during retraining). Re-train button now calls this server-side first, then hydrates the wizard with prior `_wizard_answers` so the user edits existing answers instead of retyping. `/api/wizard/complete` re-flips the flag back to true.

### Why this matters

User reported: "every time I run huntova locally the brain is forgotten, I need to re-train, only the chat history remains. Make sure every settings and brain are persistent in users local PC." Investigation showed the brain WAS persisting in `~/.local/share/huntova/db.sqlite` correctly — but two paths (stale field mapping + client-only Re-train reset) made it APPEAR forgotten. Settings persistence is otherwise intact: per-user `user_settings.data` is a JSON blob that survives reinstalls (DB lives outside the pipx venv). API keys live in the OS keychain, also persistent.

---

## 0.1.0a321 — May 2 2026 — Update-available banner in the browser dashboard + on `huntova --version`

### New

- **Browser dashboard now shows an "Update available" banner** when a newer release is on GitHub (`templates/jarvis.html:861-879` markup, `:843-908` CSS, `:1614-1683` JS). Pinned to the top of the viewport on every view (chat / leads / brain / settings / agent), shows current → latest version, an inline `pipx upgrade huntova` cmd that copies on click, and a dismiss button that snoozes the banner for 24h via `localStorage.hvUpdateDismissedUntil`. Pulsing amber pip + cyan accent so it reads as informational, not alarming. Honors the existing 6h server-side cache so the banner check is essentially free.
- **`huntova --version` now also reports update status** (`cli.py:2495-2516`). Quick way to check if you're behind without booting the server.
- **New `/api/update-status` endpoint** (`server.py:8534-8562`) wrapping `cli._is_update_available()` so the browser can read what the terminal banner already had — `{available, current, latest, command}`.

### Why

The terminal `_maybe_prompt_update` banner only fires at `huntova serve` boot. People running via `huntova daemon install` (launchd / systemd-user) or autostart never see that startup output and would silently run versions 50+ alpha releases stale. Now any browser session surfaces the update on load.

---

## 0.1.0a320 — May 2 2026 — Suggestion-pick textarea resize + greet banner restore on convo delete

### Bug fixes

- **Multi-line suggestion picks got clipped to a single dock row** (`templates/jarvis.html:1325-1342`). Programmatic value assignment (`p.value = it.prompt`) doesn't fire the `input` event, so `autoSize()` never ran for clicks on the suggestion strip — multi-line prompts (and several of the seeded suggestions are multi-line) only showed their first line in the dock until the user manually edited. Trigger `autoSize()` after the value set.
- **Deleting the currently-viewed conversation left the chat panel blank** (`templates/jarvis.html:1495-1510`). The handler cleared the feed and unset `_currentConversationId` but never re-showed the `#greet` capability-tiles banner — same restore that `_newConversation()` does. User saw an empty chat with no instructions on what to type. Now mirrors the new-conversation reset path.

---

## 0.1.0a319 — May 2 2026 — Agent SSE stream now reconnects on drop (the documented backoff was missing)

### Bug fixes

- **Agent EventSource stream silently died on any connection drop** (`templates/jarvis.html:3927-3973`). CLAUDE.md documents an "exponential backoff reconnect (1s → 30s)" but the implementation was absent — there was no `error` listener, no reconnect, no reset of the `_agentSse` reference. When the stream dropped (server restart, idle timeout, network blip, HTTP 4xx/5xx that disables EventSource auto-retry), the agent state pill, the live log stream, and lead-found notifications all silently froze even though the agent thread might still be running. Now wires the documented backoff: capped exponential 1s → 30s, resets to 1s on `open`, closes + nulls the dead handle and re-`new`s a fresh `EventSource` on `CLOSED`.

---

## 0.1.0a318 — May 2 2026 — Brainstorm chat history no longer leaks across re-trains

### Bug fixes

- **Brainstorm chat history under phase-5 question keys leaked into the next training run** (`templates/jarvis.html:2767-2799`). Phase-5 question IDs (`p5_10`, `p5_11`, …) are deterministic and reused across runs, so the persisted `localStorage.hvBrainstorm_p5_<n>` history loaded from the previous run even when the new run regenerated an entirely different question under the same id. Both Restart and Re-train now wipe every `hvBrainstorm_*` key on click via a shared `_clearBrainstormCache()` helper.

---

## 0.1.0a317 — May 2 2026 — Brain wizard select questions no longer silently default to the first option

### Bug fixes

- **Brain wizard `select` questions silently submitted the first option if the user hit Continue without touching the dropdown** (`templates/jarvis.html:3354-3380`). Native `<select>` elements auto-select their first `<option>`, and the Continue-button validation only checked for an empty string — so the value was always non-empty and the question passed silently. Worst case: the `outreach_tone` question defaulted to 'warm' without the user ever picking it. Prepended a disabled "— Choose —" placeholder option and forced the placeholder to stay selected when no saved answer matches the offered options, so the user is forced to make an explicit choice.

---

## 0.1.0a316 — May 2 2026 — Chat-history drawer can now be dismissed (ESC + outside-click)

### Bug fixes

- **Chat-history slide-out drawer had no dismiss path** (`templates/jarvis.html:1532-1572`). On mobile especially, opening the drawer covered the toggle button at top-left, so the user had no obvious way to close it again. Wired ESC and outside-click closers; clicks inside the drawer still pass through to the row handlers (`stopPropagation` on the drawer + on the toggle so the document-level closer doesn't immediately re-close the drawer we just opened).

---

## 0.1.0a315 — May 2 2026 — Chat send no longer freezes the UI when an action handler throws

### Bug fixes

- **`send()` left `_busy=true` and the send button disabled forever if any post-fetch action handler threw** (`templates/jarvis.html:1813-1973`). The cleanup line `_busy = false; sendBtn.disabled = false;` only ran on the happy path — if `appendBotPrefixed` failed mid-DOM mutation, or `fireHunt` threw, or the subagent fan-out hit an unhandled exception, the chat appeared frozen and the user had to reload to send again. Wrapped the entire post-send pipeline in `try { … } finally { reset }` so the UI always recovers.

---

## 0.1.0a314 — May 2 2026 — Drag-drop highlight is now actually visible + chat-history delete button works on touch

### Bug fixes

- **The drag-drop highlight class had no CSS rule** (`templates/jarvis.html:557-583`). a313 fixed the highlight's enter/leave flicker, but the highlight itself had been completely invisible since it shipped — `dock.classList.add('hv-drop-hot')` ran but no `.hv-drop-hot` selector existed. Drop still worked, but the user got zero feedback that the dock was a valid target. Added cyan-edged outline + soft inner glow + a "Drop image to attach" overlay so the dock visibly lights up.
- **Conversation-history delete button was unreachable on touch devices** (`templates/jarvis.html:254-269`). The `.ch-row-del` button was `display:none` and only shown on `.ch-row:hover` — no hover on phones/tablets means mobile users could never delete old conversations. Now rendered at low opacity by default, brighter on hover, and fully visible at `.55` opacity whenever the device matches `(hover:none)`.

---

## 0.1.0a313 — May 2 2026 — Drag-drop highlight no longer flickers + image-attachment blob URLs no longer leak

### Bug fixes

- **Drag-and-drop highlight on the chat dock flickered every time the cursor crossed a child element** (`templates/jarvis.html:1722-1750`). `dragleave` fires whenever the pointer exits ANY descendant of the registered element, so dragging an image over the dock made the `hv-drop-hot` highlight strobe on/off as the cursor passed over the textarea / attach button / send button. Replaced the naive enter/leave pair with a depth counter so the class only comes off when the pointer truly leaves the dock.
- **Image attachments leaked one blob URL per sent message** (`templates/jarvis.html:1782-1800`). `_uploadAttachment` mints `URL.createObjectURL(file)` for the local preview chip, but `send()` cleared `_pendingAttachments` without revoking those URLs. Each `URL.createObjectURL` pins the file blob in memory until revoked or the tab closes — so every sent image quietly held its raw bytes in memory until reload. The chip-remove handler at `:1660` already revokes on the manual-remove path; matched that on the send path too. Server-side only `a.id` is needed past the send call, so the local blob URL is safe to free.

---

## 0.1.0a312 — May 2 2026 — Brain Re-train no longer accumulates phase-5 questions

### Bug fixes

- **Re-train button silently bloated the wizard each cycle** (`templates/jarvis.html:2814-2826`). The Restart button at `:2750` already trims `_BRAIN_QUESTIONS` back to the base length so stale phase-5 questions don't carry over — Re-train was missing the same trim. Result: starting a new training cycle from the "✓ Trained" summary kept the previous run's 5 phase-5 questions on the array, then phase-5 fired again at the end and pushed 5 fresh ones, so each re-train added another 5 questions on top. Bringing both buttons in sync.
- **Dead code cleanup**: dropped `_convoListCache` (declared + populated but never read) — leftover from the abandoned "auto-resume most recent conversation on launch" path that the user reversed in a309 (`templates/jarvis.html:1421`).

---

## 0.1.0a311 — May 2 2026 — Fix unreadable black text on chat homepage (4 undefined CSS variables)

### Bug fixes

- **Capability boxes on the chat homepage rendered descriptions as black-on-near-black** (`templates/jarvis.html:11`). Root cause: `--hv-muted` was referenced 13 places (every `.hv-cap span` description, summary hints in the legend, team-grid loading state, prompt-addendum hints) but never defined in `:root`. Per CSS spec, `var(--undefined)` with no fallback resolves to the property's initial value — for `color` that's `canvastext`, which paints black on a dark surface. Defined `--hv-muted: #94a8bc` to match the existing dim slate palette.
- **Three more undefined CSS variables fixed in the same pass**: `--hv-card`, `--hv-border`, `--hv-bg-elev` — referenced by lead-detail tag wrappers (`templates/jarvis.html:2499, 2537`) and the brain review-summary modal (`:2810, 2836`) but never defined, so those cards rendered with transparent backgrounds and invisible borders. Defined to `rgba(255,255,255,.025)` / `rgba(0,229,204,.18)` / `#0d121d` respectively.
- **`--hv-mono`** also undefined — added the JetBrains Mono stack used elsewhere so the few elements pinning to `var(--hv-mono)` (capability-legend `<code>` chips) don't drop to UA defaults.

---

## 0.1.0a309 → 0.1.0a310 — May 2 2026 — Brain wizard polish + smart prefills everywhere

Two-release block making the Brain wizard feel like an assistant doing the work for you instead of a form you fill in. Covers the hello-screen launch behaviour, the deep-research loading visual, a much bigger Brainstorm sidekick, ~10x richer website-scan prefill, and (a310) smart pre-written answers on the AI-generated phase-5 deep-dive questions.

### New / rebuilt

- **Brain "Going deeper" deep-research card has a real loading visual** (a309, `templates/jarvis.html:2864-2911`) — replaced a single static text line with a centred radar/scanner animation: cyan double pulsing ring + sweep arm + pulsing brain core, "ASSIGNING YOUR AI" header, and a sub-line listing the actual sub-tasks ("reading your answers · synthesising ICP · drafting deep-dive questions"). Pure CSS, respects `prefers-reduced-motion`.
- **Launch always lands on the hello / new-chat greet screen** (a309, `templates/jarvis.html:1497-1505`) — was: reload auto-resumed the last conversation from `localStorage`, so users opened Huntova mid-thread instead of seeing the capability tiles. Now: launch handler blanks the saved id every time. Old chats stay one click away in the slide-out drawer.
- **Brainstorm AI sidekick is now a full assistant pane** (a309, `templates/jarvis.html:2918-2962`) — was: 320 px wide × 320–540 px tall, 2-row textarea, cramped. Now: 460 px × 560 px–78 vh, 3-row resizeable textarea, fuller header copy ("Brainstorm with your AI"), bigger help text. Stacks single-column under 1100 px.
- **Website scan prefills ~10x more text** (a309, `server.py:6903-6970` + `templates/jarvis.html:3074-3148`) — AI prompt overhauled to demand 5-8 sentence `business_description` (was 3-4), 90-120 word `target_clients` paragraph (was 30+), 6-12 specific services (was 3-10), 6-12 industries, 5-8 buying triggers, 4-6 value propositions, 4-6 pain points, plus a new `outreach_voice_notes` field. Adds "writing rules" forbidding fluff and demanding concrete nouns from the site. `max_tokens` 8000 → 12000, corpus excerpt 14000 → 18000 chars. Client-side prefill stitches every new field into the four long-form wizard textareas, which now open at 10 rows so the prefilled paragraphs are visible without scrolling.
- **Phase-5 deep-research questions arrive with smart pre-written answers** (a310, `server.py:7419-7549` + `templates/jarvis.html:2913-2980`) — `/api/wizard/generate-phase5` rewritten in two parts: PART 1 generate 5 questions targeting wizard gaps; PART 2 write a smart `prefill` per question (60-140 word paragraph for text, matched option for single-select, matched option-list for multi-select). Endpoint now accepts `scanData` from the client and mines industries / buying triggers / value props / pain points / certifications / social proof / voice notes when drafting prefills. Client seeds `_brainState.answers[id]` from prefill at append time with case-insensitive option matching. The user just confirms or edits — same idea as the website-scan prefill: maximise what the AI does up-front so users remember and retype as little as possible.

### Bug fixes

- **Phase-5 generator was running on stale field names** (a310, `server.py:7437-7464`) — the previous summary builder referenced `business_name` / `what_you_do` / `ideal_customer` / `pain_point` / `differentiator` / `dream_client` / `anti_customer`, none of which the brain wizard collects. Result: AI saw an empty profile every call and emitted generic questions. Rewritten to read the actual fields (`company_name`, `business_description`, `target_clients`, `services`, `buyer_roles`, `regions`, `example_good_clients`, `exclusions`, `outreach_tone`).

---

## 0.1.0a142 → 0.1.0a166 — May 1 2026 — premium landing, multi-agent, audit hardening

24-release block focused on shipping an investor-ready product surface — premium animated landing, dashboard rewrite (always-on chat with AI selector, resizable panels, multi-agent runtime), and a deep legal + security audit pass.

### New / rebuilt

- **Premium landing page** (a162) — custom animated SVG mark (rotating dashed ring + scan-sweep wedge + pulsing core), starfield + aurora bg, scroll-revealed cards, magnetic copy button, two terminal demo blocks, trust strip, final CTA. Honors `prefers-reduced-motion`. Single file, no JS framework.
- **Working install URL** (a162) — replaced dead `huntova.com/install.sh` with the canonical `https://github.com/enzostrano/huntova-public/releases/latest/download/install.sh`.
- **Dashboard rewrite** (a164) — duplicate Settings entries removed, chat is now an always-on right column (no toggle), sidebar + chat are drag-resizable with `localStorage` persistence and keyboard a11y. Floating pill collapses chat on mobile.
- **Multi-agent runtime** (a164) — `agent_runner.spawn_subagent(user_id, kind, payload)` runs background daemon threads alongside the main hunt. Two kinds: `inbox_scan`, `deep_research` (a295 removed `qualify_pool` — re-scoring pipeline not yet exposed). Capped at three concurrent per user. Live status streams over the existing per-user SSE bus. New routes `/api/subagents`, `/api/subagents/spawn`, `/api/subagents/{id}/cancel`. Dashboard renders an Active Agents grid with cancel buttons.
- **AI provider selector in chat** (a166) — small dropdown next to the chat input, persisted to `localStorage`. Routes the next chat dispatch (and any agents the chat fans out) through the selected provider via a new thread-local override in `providers.py`.
- **`spawn_agents` chat action** (a166) — chat can return `{action: "spawn_agents", agents: [{kind, payload, provider}]}` and the dashboard fans them out in parallel — different country, different AI per agent.
- **Premium-landing-style dashboard** (a164) — new chat panel + resize handles + Active Agents card use the landing's `#00e5cc / #7c5cff` palette so the two surfaces feel like one product.
- **Deployed marketing site** (a163) — live at `darkred-barracuda-643789.hostingersite.com` for share-while-pre-domain demos.

### Bug fixes (organised by audit category)

- **JSON-LD edge cases** (a142, a152, a161) — Organization `legalName` fallback, Person `name` as `{givenName, familyName}` dict, `jobTitle` as list, `email: "mailto:..."` prefix strip.
- **CSV import** (a143, a156) — Apollo title-case `Company LinkedIn URL` mapped, dedup falls back to contact_name when email empty.
- **Auto-reply detection** (a144) — IMAP autoreply check now strips stacked `Re:`/`Fwd:` prefixes before matching.
- **SMTP** (a145, a155) — `email_service` reads `HV_SMTP_PASSWORD` fallback; auth-fail no longer dumps imaplib stack trace; `/api/connect/test-smtp` no longer echoes raw provider error.
- **DMARC** (a145) — `p=quarantine` now warns instead of falsely passing as `ok`.
- **Share pages** (a146, a159, a164) — naive `expires_at` no longer keeps expired shares accessible; corrupted snapshot returns 404 instead of empty 200; `_SHARE_LEAD_FIELDS` strips `contact_role` + `contact_linkedin` (PII / GDPR Art.5(1)(b)).
- **Search** (a147, a154) — DDG fallback no longer double-decodes URLs; SearXNG snippet falls back through `content → snippet → abstract` for fork compat.
- **Provider config** (a148) — env-var values strip wrapping quotes (so `KEY="value"` from `.env` templates work).
- **Settings API** (a149) — string `"false"` no longer flips boolean settings on (`bool("false") == True` Python gotcha).
- **Email validation** (a150) — RFC 5321 double-dot reject in regex.
- **Country mapping** (a151) — `.uk` / `.co.uk` / `.org.uk` / `.ac.uk` added.
- **Encoding** (a152) — page fetcher distrusts bare `iso-8859-1` from headers, sniffs via `apparent_encoding`.
- **Update probe** (a157) — alpha sorts below stable in version comparison; download-page copy button has Safari fallback via `document.execCommand('copy')`.
- **Account API** (a158) — `display_name` capped at 200 chars; IMAP SINCE date uses hardcoded English month abbrs (locale-portable).
- **Memory restore** (a159) — corrupted JSON archive emits stderr warning.
- **Pulse** (a160) — TTY-aware ANSI color helper (no escape-code leak when piped); plugins settings deep-merge on partial updates.
- **Recipe + scoring** (a161) — `recipe run --max-leads N` overrides saved cap.
- **Investor-readiness pass** (a165) — paste-key embedded newlines stripped, `/api/chat` whitelists actions before return, `[INSTR]` debug prints gated behind `HV_INSTR=1`, dashboard credits-pill no longer flashes em-dash, mobile install card stacks at ≤480px.

### Privacy / legal

- **Telemetry off by default** (a164) — `config.DEFAULT_USER_SETTINGS["telemetry_opt_in"]` flipped from True → False so the "0 data sent to huntova" claim on the landing is literally true on a fresh install.
- **Public share fields stripped further** (a164) — no `contact_role`, no `contact_linkedin` in `/h/<slug>` snapshots.
- **Landing claims tightened** (a163, a164, a165) — competitor brand names removed entirely; "no middleware ever sees your data" replaced with the more honest "telemetry off by default + nuance about opt-in shares"; "14 pages" → "up to 14 pages".
- **AGPL §3(b) wheel compliance** (a164) — `setup.py` now bundles `LICENSE`, `NOTICE.md`, `PRIVACY.md`, `TERMS.md`, `SECURITY.md` into `share/huntova-meta/` so re-distributors of the wheel ship the license.
- **Legacy SaaS templates deleted** (a163) — `download.html`, `account.html`, `admin.html`, `try.html` removed; `_COMPARE_DATA` + `/compare/<name>` purged.

### Security

- **SSRF guard on the page fetcher** (a164) — `fetch_page_requests()` + `crawl_prospect()` reject `127.0.0.1`, RFC1918, `169.254.169.254` before opening sockets. Closes SERP-poisoning vector.
- **Webhook URL validation** (a164) — Slack + Generic plugins use `urlparse` + scheme allowlist + private-IP block instead of `lower().startswith()`.
- **`/api/lead-feedback`** (a164) — `reason` field capped at 500 chars.
- **`install.sh`** (a164) — stale `v0.1.0a12` header comment removed.

---

## 0.1.0a110 → 0.1.0a132 — May 1 2026 — autonomous local-first cold-email stack

23-release sweep that takes Huntova from "lead-gen agent" to a full
local-first replacement for Apollo / Clay / Hunter / Instantly /
Lemlist / Mailmeteor / GlockApps / Mailshake.

### New subcommands

- **`huntova quickstart`** (a132) — single-command interactive
  walkthrough: pick playbook → run first hunt → preview drafts →
  next-step list. Sub-30-second time-to-first-lead for new
  installs.
- **`huntova research <id>` / `--batch N --above SCORE`** (a116, a123)
  — deep-research one or N leads. 14-page crawl per lead, AI
  rewrites the opener with a hook the prospect would recognise.
- **`huntova sequence run / status / pause`** (a113) — 3-step
  follow-up cadence (Day +4 bump, Day +9 final). Auto-enrolled by
  `huntova outreach send`, auto-paused on reply detection.
- **`huntova inbox setup / check / watch`** (a112) — IMAP poll,
  matches incoming `In-Reply-To` to outbound `_message_id`s, flips
  matched leads to `email_status=replied`. Now also AI-classifies
  every reply (a120) into `interested / not_now / not_interested /
  out_of_office / wrong_person / unsubscribe` and routes status
  + cadence + DNA feedback accordingly.
- **`huntova doctor --email`** (a114) — SPF / DKIM / DMARC / MX
  pre-flight; `dnspython` is now a base dep (a124).
- **`huntova pulse [--since 7d]`** (a122) — weekly self-coaching
  summary with concrete next-action recommendations.
- **`huntova schedule print --target launchd|systemd|cron`** (a128)
  — emits OS-native scheduled-job config for daily auto-runs of
  sequence + inbox + pulse. Doesn't auto-install (no silent
  persistence) — user pipes the snippet into the right place.
- **`huntova playbook` / `huntova playbooks`** (a127) — friendlier
  alias for `huntova examples`.

### Existing surfaces extended

- **Chat as brain** (a111, a125, a126) — both `/api/chat` (web) and
  `huntova chat` (CLI) now drive the entire stack via plain English:
  `update_settings`, `update_icp`, `set_lead_status`, `delete_lead`,
  `mint_share`, `research`, `sequence_run`, `sequence_status`,
  `inbox_check`, `pulse`, `playbook_install`, `playbook_ls`,
  `navigate`, `start_hunt`, `list_leads`, `answer`. The model
  receives live state in the system prompt so it doesn't have to
  guess current settings.
- **`huntova outreach send --research-above SCORE`** (a118) —
  auto-runs deep-research on every lead whose `fit_score` crosses
  the threshold *before* sending the opener. The previous draft
  archives into `rewrite_history` for one-click revert.
- **`huntova ls`** (a124) — three new shortcuts: `--status`,
  `--reply-class`, `--min-fit`. Combinable.
- **`huntova status`** (a130) — three new rows: sequence step
  counts, IMAP configured Y/N, daily-schedule installed Y/N. When
  missing, prints the exact subcommand to fix.
- **10 bundled playbooks** (a115) — `agencies-eu`,
  `b2b-saas-hiring`, `tech-recruiting`, `ecommerce-shopify`,
  `solo-coach`, `consultant-fractional`, `video-production`,
  `saas-developer-tools`, `design-studio`, `podcast-producer`.
  Install auto-seeds wizard `business_description`, `target_clients`,
  and `default_tone`.

### Repo housekeeping

- a110 deleted 103 stale `RELEASE-v0.1.0a*.md` files. CHANGELOG.md
  is the durable record; per-release notes are written transiently
  for the GitHub release body and gitignored.
- Added `PRIVACY.md` and `TERMS.md`.
- Removed dev-only planning files (`NEXT_PHASE_PLAN.md`,
  `REDESIGN.md`, `ROADMAP.md`, `STRATEGY.md`,
  `STABILITY-SWEEP-2026-04-30.md`, `CHECKPOINT.md`).
- Purged 40 MB+ of build artefacts and a stale orphan
  `frontend/node_modules/`.

### Bug fixes

- a131: Message-ID strip uses `.strip("<>")` (substring) at three
  sites — `lstrip("<").rstrip(">")` was a character-set strip that
  could mangle nested-bracket IDs from some ESPs.
- Many smaller fixes across the new modules caught by parallel
  bug-hunt agents during the sprint.

---

## 0.1.0a85 → 0.1.0a109 — May 1 2026 — extraction + storage hardening sprint

25-release sweep focused on quality of the data the agent extracts, the integrity of state once stored, and the privacy/UX edges around shared lead pages.

### Extraction & search

- LinkedIn URL regex now matches country-code subdomains (`uk.linkedin.com`, `de.linkedin.com`, …).
- Page-text strip runs `html.unescape()` after tag removal — the AI sees `Acme & Co.` instead of `Acme &amp; Co.`.
- `crawl_prospect._fetch` reads `Content-Type` and bails on PDF / image / video / octet-stream / zip *before* HTML regex (was producing binary garbage that polluted scoring text).
- `_search_session` now sets a real Huntova `User-Agent` + JSON-first `Accept` so SearXNG instances stop 403'ing the requests-bot UA.
- `_hostish_netloc` strips an explicit port (`:443`/`:8080`) before block matching.
- `dedup-by-domain` plugin lowercases the host before the `www.` strip.
- `_NOREPLY_RE` covers `do-not-reply`, `do_not_reply`, `automated`, `auto-reply` (in addition to the existing variants) so those addresses never land in `contact_email`.
- `validate_email` accepts the `Name <email@domain>` capture form so JSON-LD `ContactPoint` blocks yield real emails.
- Jina fallback explicitly logs the 429 case so operators can tell why JS-heavy sites stop extracting.

### Scoring quality

- Country case-folded (`"france"` / `"FRANCE"`) before `eu_countries` set lookup.
- Leadership-role regex got `\b…\b` so "directorate" / "CEOgrade" stop matching as the contact role.
- `is_recurring` keyword tuple includes `annual / yearly / weekly / every {year,month,week}`.
- `_clean_subject` strips `Re:` / `Fw:` / `Fwd:` prefixes (cold emails stop looking like replies).
- `BANNED_WORDS` covers the `i hope this finds you` / `hope you're doing well` / `just wanted to` family.
- Strict `_coerce_bool` for `is_recurring` / `is_virtual_only` — string `"false"` no longer coerces to `True`.
- `event_type` deduped against `event_name` when the AI restates the same string for both.
- Free-form summary fields (`why_fit`, `evidence_quote`, `production_gap`) capped at 240 chars so chatty AI responses don't blow out the row layout.
- Team-page role extraction strips leading articles ("the head of sales") and trailing punctuation.

### Storage correctness

- `upsert_lead ON CONFLICT` preserves user-set `email_status` (no more reset to `"new"` on re-discovery) and keeps `GREATEST(fit_score)` (no more downgrade on re-encounter).
- `status_history` skips consecutive duplicates and trims to last 100 (timeline stops becoming wallpaper).
- `_run_log` capped at 1 000 entries (used to grow unbounded across long hunts).

### API & endpoints

- `/h/<slug>` share pages set `Cache-Control: private, no-cache, no-store, must-revalidate` + `X-Robots-Tag: noindex, nofollow`.
- `/api/wizard/scan` sets full no-cache / private headers.
- `/api/update` 404 returns canonical `{"ok": false, "error": "not found"}` shape.
- `/api/update` caps `notes` at 4 000 chars.
- CSV export ships UTF-8 BOM + `charset=utf-8` (Excel mojibake on accented org names fixed).
- `/api/export/json` adds `default=str` so a stray `datetime`/`Decimal` no longer crashes export.
- `/api/export/account-data` switched to UTC for `exported_at` and the filename day component.
- `/api/rewrite` whitelists `tone` against `friendly/consultative/broadcast/warm/formal`.
- `_all_emails_found` excludes the chosen `contact_email` (no more accidental double-mail).
- Provider-test error message redacts the API key (`***redacted***`) before formatting.

### UI / agent runner

- Email rewrite-history "revert" passes the original-array index (newest-first display, but reverts the version actually clicked).
- Inline edit-save preserves an explicitly-cleared subject/body (no more falling back to old value on `||` short-circuit).
- Wizard `.iwiz-assist-input` font-size 16 px in the `<600px` breakpoint (iOS auto-zoom on focus stopped).
- `stop_agent()` removes the user from the queue so cancelling while queued no longer kicks off the hunt later.

### CLI / packaging

- `huntova rm` returns exit 1 when the user types `n` at the confirm prompt (no more silent "success").
- SMTP `_send_email_sync` branches on port 465 → `SMTP_SSL` (cloud users on 465-only providers can now send mail).
- `huntova migrate` exits 1 on zero imports + writes `[huntova] no valid rows imported` to stderr.
- `huntova benchmark` only targets actually-configured providers (no more bogus auth-failed rows for keys the user never set).
- Landing-page install one-liner uses explicit `https://`.

---

## 0.1.0a30 → 0.1.0a84 — Apr 30 → May 1 2026 — local-first BYOK pivot + agent-driven bug hunt

Pivot from hosted SaaS to local-first BYOK CLI distributed via pipx + GitHub Releases. Capability flags in `runtime.py` collapse billing/auth/SMTP/OAuth in local mode.

Highlights:
- Anthropic Claude becomes the BYOK default provider; 13 providers wired (`providers.py`).
- OS keychain-first secrets storage with encrypted-file fallback (`secrets_store.py`).
- SQLite singleton-conn driver with an application-level `_SqliteSerial` lock (a34) — 100/100 concurrency stress test passes.
- SearXNG decoupled — public instance default for local, sidecar default for cloud.
- Plugins: `csv-sink`, `dedup-by-domain`, `slack-ping`, `generic-webhook`, `recipe-adapter`, `adaptation-rules` ship in the wheel.
- Agent runner: `MAX_CONCURRENT_AGENTS = 1`, per-user queue, terminal-state SSE event on completion.
- Share pages, recipes, daemon installer (launchd / systemd-user), shell-completion installer, memory subcommand, migrate, approve, teach, logs, benchmark.
- Roughly 50 distinct fixes shipped via the agent-driven hunt loop (CSV formula injection defense, SSE bus dead-sub sweep, `_atomic_write_0600`, JSON-LD `@graph` envelope, refreshed-lead dedup, GREATEST→MAX SQLite translation, year regex tightening, …).

## 0.1.0a3 → 0.1.0a29 — Apr 30 → mid-Apr 2026 — TUI, daemon, memory, migrate, approve

Onboard wizard ergonomics, OS-native daemon installer, memory subcommand, CSV migrate from Apollo / Clay / Hunter, manual approve queue.

## 0.1.0a1 → 0.1.0a2 — first public alpha

Initial public release. Local CLI shape, SearXNG-driven hunt, BYOK provider abstraction, FastAPI dashboard.
