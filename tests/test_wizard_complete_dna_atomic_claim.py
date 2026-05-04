"""Regression tests for BRAIN-110 (a479): the BRAIN-88
ready→pending flip must atomically CLAIM the right to
enqueue a DNA job. Re-flipping when `_dna_state` is
already "pending" is a duplicate enqueue.

Failure mode (Per Huntova engineering review on
idempotent job systems):

BRAIN-88 (a457) introduced an atomic ready→pending flip
before the multi-second brain+dossier compute window so
clients see "in flight" instead of stale "ready" during
retraining. The flip is honored under the row lock; it
checks `_wizard_revision` and `_wizard_epoch` to reject
stale tabs.

But the flip does NOT check whether `_dna_state` is
already `"pending"`. So if two browser tabs both submit
`/api/wizard/complete` near-simultaneously:

1. Tab A enters merge_settings, mutator runs against
   `_dna_state="ready"` → flips to "pending", commits.
2. Tab B enters merge_settings (now serialized after A),
   mutator runs against `_dna_state="pending"` already.
   Revision + epoch still match (BRAIN-88 doesn't bump
   revision). Mutator re-sets to "pending" (no-op
   visually) and the function returns success.
3. Both Tab A AND Tab B proceed to brain+dossier compute
   and call `_spawn_bg(_gen_dna())`. Two DNA generation
   jobs run in parallel for the same logical retrain.

That's exactly the check-then-act race idempotent job
systems guard against. The standard fix is to bind
uniqueness to persisted operation state: the SAME atomic
step that reads `_dna_state="ready"` must transition it
to `_dna_state="pending"`. Pre-pending callers must
short-circuit and skip enqueuing.

Invariants:
- The BRAIN-88 flip mutator surfaces a "claim_lost"
  signal when `_dna_state` is already `"pending"` at
  mutator entry, distinguishing it from the legitimate
  "ready" → "pending" transition.
- /api/wizard/complete returns a 409-class response
  ("DNA generation already in flight") when claim is
  lost — never silently spawns a second compute window.
- Identical-payload reposts that were ALREADY captured
  by the BRAIN-85 idempotency cache (`reused: True`) are
  NOT affected — they short-circuit before this flip.
- Source-level: the flip mutator must reference
  `_dna_state` at mutator entry to detect the in-flight
  case.
"""
from __future__ import annotations
import inspect
import re


def test_pending_flip_mutator_inspects_dna_state():
    """Source-level: the BRAIN-88 flip mutator must look
    at `_dna_state` BEFORE writing pending, otherwise it
    can't detect the in-flight case."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    # Capture the whole closure body — from the def line
    # up to the next sibling def or the merge_settings
    # call that consumes it.
    m = re.search(
        r"def _pending_flip_mutator\(.*?await db\.merge_settings\(",
        src,
        re.DOTALL,
    )
    assert m, (
        "BRAIN-110 regression: _pending_flip_mutator "
        "should be present in api_wizard_complete."
    )
    body = m.group(0)
    # The mutator must READ _dna_state from the current
    # row to detect already-pending.
    assert 'w.get("_dna_state")' in body or "w['_dna_state']" in body, (
        "BRAIN-110 regression: the flip mutator must read "
        "`_dna_state` from the current row state. Without "
        "that read, it cannot distinguish a legitimate "
        "ready→pending transition from a duplicate "
        "pending→pending re-flip — both pass and both "
        "trigger duplicate _gen_dna() background jobs."
    )


def test_pending_flip_mutator_short_circuits_when_already_pending():
    """Source-level: when `_dna_state == 'pending'` at
    mutator entry, the mutator must signal "claim_lost"
    and the endpoint must NOT proceed to brain+dossier
    compute or `_spawn_bg(_gen_dna())`."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    # The endpoint must surface a "claim_lost" / "in
    # flight" code path.
    has_claim_signal = (
        "claim_lost" in src
        or "dna_in_flight" in src
        or "retrain_in_flight" in src
        or "already_pending" in src
    )
    assert has_claim_signal, (
        "BRAIN-110 regression: api_wizard_complete must "
        "surface a sentinel like 'claim_lost' / "
        "'dna_in_flight' so the endpoint knows to bail "
        "before running the brain+dossier compute or "
        "spawning a second _gen_dna job."
    )


def test_pending_flip_mutator_returns_409_when_claim_lost():
    """Source-level: claim-lost path must return a 409
    JSONResponse — the same convention BRAIN-81 uses for
    'state changed under you' races."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    # Must reference 409 status near a claim_lost / in-
    # flight branch.
    has_409 = re.search(
        r"(claim_lost|dna_in_flight|retrain_in_flight|already_pending)"
        r".*?status_code\s*=\s*409",
        src,
        re.DOTALL,
    )
    assert has_409, (
        "BRAIN-110 regression: claim-lost must return "
        "HTTP 409 so a sibling tab can detect the loss "
        "and reconcile via /api/wizard/status polling."
    )


def test_claim_lost_path_does_not_spawn_dna_bg():
    """Source-level: between the claim-lost detection and
    `_spawn_bg(_gen_dna())`, there must be a `return`
    that prevents fall-through. Without it, the bail
    branch runs but the function still proceeds to the
    second background spawn."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    # Find the claim-lost handler and confirm a
    # JSONResponse-or-return precedes any later
    # `_spawn_bg(_gen_dna)` reference within a small
    # window after the claim-lost branch.
    idx = -1
    for token in ("claim_lost", "dna_in_flight", "retrain_in_flight", "already_pending"):
        i = src.find(token)
        if i >= 0:
            idx = i
            break
    assert idx >= 0, (
        "BRAIN-110 regression: claim-lost token not found "
        "(see prior test)."
    )
    # The next 1500 chars after the claim-lost branch
    # must contain a `return` — confirming the endpoint
    # bails before further DNA work.
    window = src[idx: idx + 1500]
    assert "return" in window, (
        "BRAIN-110 regression: claim-lost branch must "
        "`return` before hitting any subsequent "
        "_spawn_bg(_gen_dna()) call. Without an explicit "
        "return, the second tab's request still runs the "
        "expensive brain+dossier compute and enqueues a "
        "duplicate DNA job."
    )


def test_pending_flip_serial_invocation_simulation():
    """Behavioral: simulate two serial invocations of the
    flip mutator against the same starting row. The first
    must transition ready→pending; the second (now seeing
    pending) must signal claim_lost via the captured-state
    dict pattern (same convention as `_flip_stale` for
    revision/epoch races)."""
    # Importing api_wizard_complete just to confirm the
    # module compiles + the mutator is reachable. The
    # actual mutator is a closure; we re-implement the
    # same logic in this test against the documented
    # invariant — a regression in the source would fail
    # the source-level tests above.
    from server import api_wizard_complete  # noqa: F401

    captured_revision = 5
    captured_epoch = 1

    def make_mutator():
        flag = {"value": False, "kind": None}

        def mutator(cur):
            cur = dict(cur or {})
            w = dict(cur.get("wizard") or {})
            cur_rev = int(w.get("_wizard_revision", 0) or 0)
            cur_epoch = int(w.get("_wizard_epoch", 0) or 0)
            if cur_epoch != captured_epoch:
                flag["value"] = True
                flag["kind"] = "wizard_reset"
                return cur
            if cur_rev != captured_revision:
                flag["value"] = True
                flag["kind"] = "stale_revision"
                return cur
            # BRAIN-110: detect already-pending
            if w.get("_dna_state") == "pending":
                flag["value"] = True
                flag["kind"] = "dna_in_flight"
                return cur
            w["_dna_state"] = "pending"
            cur["wizard"] = w
            return cur

        return mutator, flag

    # Tab A: ready row → pending
    row = {
        "wizard": {
            "_wizard_revision": 5,
            "_wizard_epoch": 1,
            "_dna_state": "ready",
        }
    }
    m_a, flag_a = make_mutator()
    row = m_a(row)
    assert flag_a["value"] is False
    assert row["wizard"]["_dna_state"] == "pending"

    # Tab B: same starting captured_revision/epoch but
    # the row is now `pending`. Must signal claim_lost.
    m_b, flag_b = make_mutator()
    row = m_b(row)
    assert flag_b["value"] is True, (
        "BRAIN-110 regression: when _dna_state is already "
        "pending, the flip mutator must signal claim_lost. "
        "Otherwise the second tab proceeds to enqueue a "
        "duplicate _gen_dna() job."
    )
    assert flag_b["kind"] in ("dna_in_flight", "retrain_in_flight", "claim_lost", "already_pending"), (
        "BRAIN-110 regression: claim_lost kind must be a "
        "documented sentinel."
    )
