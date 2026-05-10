"""Regression test for BRAIN-68 (a429): /api/wizard/save-progress
optimistic-concurrency revision guard.

Failure mode (per GPT-5.4 audit on multi-tab wizard race class):

- Tab A and tab B both load wizard at revision N.
- Tab A edits answer X and clicks Continue → save-progress fires,
  server bumps revision to N+1.
- Tab B (still showing pre-edit state — never refreshed) edits the
  SAME field with stale content and clicks Continue. Save-progress
  unconditionally accepts the write and bumps revision to N+2.
  Tab A's newer answer is silently overwritten.

This is exactly the lost-update class that optimistic concurrency
exists to detect. Pre-fix, BRAIN-14 (a375) added a revision guard
on /api/wizard/complete only — but only because the brain-build
window is multi-second so the lost-update window was wide. The
SAME RACE EXISTS on save-progress, just with a smaller window
(network round-trip ~50-500ms is enough for the user to be
typing-ahead in another tab).

Server-side hardening (atomic merge_settings + monotonic phase)
prevents JSON corruption, but doesn't detect that tab B's
"answers" payload was generated against a stale view of the
world. The fix per GPT-5.4: save-progress must accept an
`expected_revision` from the client; if provided AND mismatches
stored revision, return 409 Conflict so the client can prompt
the user to refresh instead of silently destroying tab A's work.

Backwards compatibility: clients that don't send
`expected_revision` keep working (the field is optional). Old
huntova installs in the wild don't break.
"""
from __future__ import annotations
import inspect


def test_save_progress_accepts_expected_revision_from_client():
    """Source-level: the save-progress mutator must read the
    client-provided `expected_revision` from the request body."""
    from server import api_wizard_save_progress
    src = inspect.getsource(api_wizard_save_progress)
    assert "expected_revision" in src, (
        "BRAIN-68 regression: save-progress must accept an "
        "`expected_revision` from the client to detect multi-tab "
        "lost-update races. Without it, a stale tab silently "
        "overwrites a newer tab's answers — the exact bug class "
        "optimistic concurrency is supposed to catch."
    )


def test_save_progress_returns_409_on_revision_mismatch():
    """Source-level: the save-progress endpoint must return HTTP
    409 (Conflict) when the client's expected_revision doesn't
    match the stored revision. 409 is the standard
    optimistic-concurrency rejection code, distinct from 429
    (rate limit) so the frontend can show the user a different
    toast: 'Refresh — your wizard was edited in another tab.'"""
    from server import api_wizard_save_progress
    src = inspect.getsource(api_wizard_save_progress)
    assert "409" in src, (
        "BRAIN-68 regression: save-progress must return HTTP 409 "
        "on revision mismatch. Falling back to silent acceptance "
        "would let multi-tab users lose work without warning."
    )


def test_save_progress_returns_current_revision_in_response():
    """Source-level: the success response must include the
    post-save revision so the client can update its tracked
    revision and stay aligned for the next save. Without this,
    the second save from the same tab would always 409."""
    from server import api_wizard_save_progress
    src = inspect.getsource(api_wizard_save_progress)
    # The success path returns a dict — the revision must be in it.
    # Look for `"revision"` or `'revision'` as a key in the return.
    assert ('"revision"' in src or "'revision'" in src), (
        "BRAIN-68 regression: save-progress success response must "
        "include the post-save revision so the client tracks the "
        "current value and doesn't false-409 on its own next save."
    )


def test_client_sends_expected_revision_with_save_progress():
    """The wizard JS must include `expected_revision` in the
    save-progress fetch body for the guard to be effective.
    Server-side enforcement alone doesn't help if the client
    never sends the token."""
    with open("templates/jarvis.html", "r", encoding="utf-8") as fh:
        src = fh.read()
    # Anchor on the unique BRAIN-67 token to find the Continue
    # handler block; the same handler is where save-progress is
    # called with the new expected_revision field.
    anchor = "_brainSaveSeq"
    idx = src.find(anchor)
    assert idx != -1
    block = src[idx:idx + 4000]
    assert "expected_revision" in block, (
        "BRAIN-68 regression: the Continue handler must send "
        "`expected_revision` with every save-progress fetch. "
        "Without it, the server-side 409 guard is dead code."
    )


def test_client_handles_409_with_refresh_toast():
    """When the server returns 409, the client must surface a
    distinct toast prompting the user to reload — NOT the generic
    'Save failed' that would imply retry-without-reload (which
    would just 409 again forever)."""
    with open("templates/jarvis.html", "r", encoding="utf-8") as fh:
        src = fh.read()
    anchor = "_brainSaveSeq"
    idx = src.find(anchor)
    block = src[idx:idx + 4000]
    # Look for an explicit 409 branch. Must reference 'reload' or
    # 'refresh' so the user understands the recovery action.
    has_409_branch = ("409" in block) and (
        "reload" in block.lower() or "refresh" in block.lower()
    )
    assert has_409_branch, (
        "BRAIN-68 regression: the 409 response path must surface "
        "a 'reload to see latest' toast, distinct from the "
        "generic save-failure path. Otherwise the user retries "
        "in a loop and never realizes another tab is the conflict."
    )
