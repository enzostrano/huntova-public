# v0.1.0a245

## Updates — error-code database + silent-bail surfacing for hunts fired from chat

### Why this release exists
Until now, when you typed *"start a hunt"* into chat and the agent immediately bailed (SearXNG offline, API key dead, brain wizard skipped), the chat reply was a generic *"Hunt did not start. Check Agent."* — and the Agent panel itself just said `idle` with zero context. You had to dig into SSE logs to see why. That was the exact frustration in `i asked for a hunt it says startign and never did, no error messages or similar you know`.

### What changed
- **`/api/status` now returns `last_status` and `last_state`.** `UserAgentContext` caches the most recent `emit_status` call so any client polling without an SSE subscription can see *why* the agent went idle. Fixes `user_context.py` + `server.py:6583`.
- **Chat dispatcher polls /api/status twice (1.4s, 3.5s) after firing a hunt.** If the agent terminal-states inside that window, the chat surface posts the actual reason as a follow-up reply with a tailored fix hint, instead of pretending the hunt is alive.
- **Stable error codes.** Status messages that block a hunt now include `HV-E1001` (SearXNG offline), `HV-E1002` (AI service unavailable), `HV-E1004` (Brain wizard incomplete). Frontend matches the code first, falls back to keyword scan.
- **`docs/ERROR_CODES.md` shipped.** 24 codes across hunt lifecycle / chat / brain / settings / CRM / SSE / install. Each has a one-line fix the user can act on. Append-only — never renumber. Categories: `HV-E1xxx` Hunt, `HV-E2xxx` Chat, `HV-E3xxx` Brain, `HV-E4xxx` Settings, `HV-E5xxx` CRM, `HV-E6xxx` SSE, `HV-E7xxx` Install.

### Concrete user-visible flow
```
You:    start a hunt
Bot:    → Agent dispatched. Watching the agent panel...
        (1.4s later)
Bot:    ✗ Hunt stopped: HV-E1001 Search engine offline — install SearXNG or set SEARXNG_URL
        Fix: install SearXNG locally (`huntova doctor`) or set SEARXNG_URL to a working instance.
```

## Known issues
- Continual-learning gap #2 still queued (cached DNA refresh during running hunt).
- Phase-5 AI-generated deep-dive Qs not yet integrated into Brain wizard.
- `cmd_research` doesn't honor `cancel_event` mid-flight.
