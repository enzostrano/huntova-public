# Huntova v0.1.0a49 — 2026-05-01

Two more agent-found bugs. One real cloud-mode security finding,
one plugin-loader robustness fix.

## Bug fixes

### `/api/hunts/share` validates run_id ownership
- Was: the endpoint filtered `lead_ids` by the caller's user_id but
  let any `run_id` from the request body through unchecked. User A
  could mint a share that named user B's run as the source —
  leaking ownership metadata + skewing analytics.
- Now: any non-null `run_id` is fetched from `agent_runs` and the
  row's `user_id` must match the caller. Mismatch → silently drop
  the run_id rather than reject the whole request, since old
  clients may send stale run_ids on retry. Lead-level filtering
  was already correct.

### `plugins.py` per-plugin register try/except
- Was: `_load_local_scripts` had ONE outer try/except wrapping the
  whole `for attr in dir(mod)` loop. If plugin B's `register()`
  raised, the loop broke and plugins C, D, ... in the same file
  were silently dropped.
- Now: register + append are wrapped in their own try/except. One
  bad plugin no longer blocks its siblings. Errors land in
  `_load_errors` per-plugin so `huntova doctor` can surface them.

## Updates
- None.

## Known issues
- Same as a48.
