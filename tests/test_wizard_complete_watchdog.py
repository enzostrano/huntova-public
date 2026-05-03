"""Regression tests for BRAIN-72 (a433): /api/wizard/complete must
terminate within a bounded time. No upstream/synchronous-compute
hang can hold the request open indefinitely.

Failure mode (per GPT-5.4 hung-provider/watchdog audit):

`api_wizard_complete` (`server.py:7663+`) ran:

    brain = _build_hunt_brain(_w_snap)         # synchronous, on event loop
    dossier = _generate_training_dossier(...)  # synchronous, on event loop

Two layered problems:

1. **Synchronous on the event loop**. Both calls are pure-Python
   compute that can take seconds on large profiles (hundreds of
   fallback queries, deep dossier generation). Running them
   directly from the async handler blocks the event loop for
   every other user — equivalent to a synchronous DoS. Should be
   in `asyncio.to_thread` so other requests keep moving.

2. **No watchdog**. If either function hangs (a slow library
   call, a regex pathological input, a deeply-nested wizard
   blob from a hand-edited save), the request sits there until
   the upstream proxy (~60s on Hostinger / Railway) or browser
   fetch timeout (~30s) kills it. User sees "spinner forever"
   then ambiguous error → believes completion failed → may
   double-click → racing background DNA generation already
   running (BRAIN-10 rate-limit catches that, but the user
   experience is still terrible).

Plus when the proxy 504s, the user has no way to know whether
the merge txn committed or not — the answer was "still saved
via save-progress, no derived artifacts" but they can't tell.

Invariants:
- Brain+dossier computation must run inside `asyncio.to_thread`.
- The combined call must be wrapped in `asyncio.wait_for` with a
  bounded timeout (e.g. 45s — tighter than typical proxy 504).
- On timeout: return 504 with a clear, atomic error message.
  Critically: NO derived-artifact merge must have committed.
  Save-progress's persisted answers stay intact.
- 504 response must include enough info that the client can
  surface a "retry" action without losing the user's answers.
"""
from __future__ import annotations
import inspect


def test_complete_runs_brain_and_dossier_off_event_loop():
    """Source-level: `_build_hunt_brain` and
    `_generate_training_dossier` must be invoked via
    `asyncio.to_thread` (or similar) so the event loop stays
    responsive for other users during the multi-second compute."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    # The fix should wrap the brain+dossier compute in to_thread.
    # Either inline calls or via a helper named _build_artifacts /
    # _wizard_build_artifacts / similar.
    has_off_loop = (
        "to_thread" in src
        and ("_build_hunt_brain" in src or "_build_artifacts" in src
             or "_wizard_build" in src)
    )
    assert has_off_loop, (
        "BRAIN-72 regression: brain+dossier compute must run via "
        "asyncio.to_thread (or equivalent) so it doesn't block the "
        "event loop. Synchronous pure-Python work directly in an "
        "async handler stalls every other user's request."
    )


def test_complete_wraps_artifact_build_in_timeout_watchdog():
    """Source-level: the brain+dossier build must be wrapped in
    `asyncio.wait_for(...)` with a bounded timeout. Without it,
    a hung compute (or a slow library call buried inside) holds
    the request open until the proxy 504s — which gives the user
    no actionable error and no clarity on what state was saved."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    has_watchdog = "wait_for" in src
    assert has_watchdog, (
        "BRAIN-72 regression: complete must use asyncio.wait_for "
        "to bound the brain+dossier compute time. A hung compute "
        "produces 'spinner forever' → user double-clicks → "
        "racing background DNA generation. Bound it explicitly."
    )


def test_complete_defines_timeout_constant_with_reasonable_value():
    """Source-level: the timeout must be a named constant (so
    operators can tune it) and must be tighter than typical
    upstream proxy 504 (Hostinger/Railway ~60s)."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    has_constant = (
        "_WIZARD_COMPLETE_TIMEOUT" in src
        or "_COMPLETE_BUILD_TIMEOUT" in src
        or "_BUILD_ARTIFACTS_TIMEOUT" in src
        or "_BRAIN_BUILD_TIMEOUT" in src
    )
    assert has_constant, (
        "BRAIN-72 regression: the watchdog timeout must be a "
        "named module-level constant. Magic numbers in async "
        "handlers are pain to tune later."
    )


def test_complete_returns_504_on_watchdog_timeout():
    """Source-level: timeout path must return HTTP 504 (Gateway
    Timeout) so the client can distinguish it from 409 (stale-write),
    429 (rate-limit), and 500 (validation/AI error). 504 is the
    standard 'upstream took too long' code; the client can show a
    'retry' toast that's distinct from the error toasts."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    has_504 = "504" in src
    assert has_504, (
        "BRAIN-72 regression: watchdog timeout path must return "
        "504. Falling back to 500 conflates AI errors with "
        "watchdog hits, which prevents the client from showing "
        "an accurate 'taking too long, retry' message."
    )


def test_complete_504_path_does_not_commit_partial_state():
    """The watchdog must fire BEFORE the merge_settings call, so
    no partial brain/dossier ever gets committed on timeout. The
    user's save-progress answers stay intact and can be used on
    retry."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    # Verify the timeout handler exists BEFORE the merge_settings
    # call. This is best-checked by source ordering: the
    # `wait_for` and 504 return must come before
    # `db.merge_settings`.
    wf_idx = src.find("wait_for")
    merge_idx = src.find("merge_settings")
    assert wf_idx != -1 and merge_idx != -1
    assert wf_idx < merge_idx, (
        "BRAIN-72 regression: wait_for (and the 504 return path) "
        "must run BEFORE merge_settings. If watchdog fires AFTER "
        "the merge has committed, the user has partial derived "
        "artifacts but the timeout response says it failed → "
        "split-brain state."
    )
