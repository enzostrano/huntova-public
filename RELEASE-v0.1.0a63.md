# Huntova v0.1.0a63 — 2026-05-01

Three more agent-found bugs.

## Bug fixes

### `agent_runner._run_agent_thread` finally clears stale agent_ctrl
- The `finally` block popped `self._running[user_id]` and reset
  `ctx.agent_running = False` but never cleared `ctx.agent_ctrl`.
  If a user clicked Stop or Pause in the millisecond before the
  thread exited, that action survived into the next agent run for
  the same user — first hunt would silently abort or pause.
- `start_agent` and `_process_queue` already reset the action
  defensively before spawning, so this was theoretical, but the
  invariant "a finished agent holds no pending ctrl" is now
  enforced at exit too.

### `templates/setup.html` tab change resets provider selection
- Was: switching from Cloud → Local → Cloud kept
  `state.selectedProvider` pointing at the original Cloud pick.
  Continue button stayed enabled. User clicked it expecting Local
  Ollama and got Anthropic Cloud.
- Now: tab click resets `state.selectedProvider = null`, disables
  Continue, and clears `.selected` from every provider card.
  User is forced to re-pick after a tab switch.

### `cli.py recipe export` friendly error on permission failure
- Was: `p.parent.mkdir() / p.write_text()` raised an unhandled
  traceback if the user passed `--out` to a read-only path or
  full disk.
- Now: wrapped in try/except for `OSError | PermissionError`,
  emits `[huntova] cannot write recipe to <path>: <ErrorType>: <msg>`
  and returns exit code 1.
- Also: explicit `encoding="utf-8"` on the write so a recipe with
  non-ASCII content doesn't depend on the platform's default.

## Updates
- None.

## Known issues
- Same as a62.
