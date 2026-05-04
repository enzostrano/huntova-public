"""Regression tests for BRAIN-126 (a495): the conflict-
response contract from BRAIN-124/125 must be codified
as a shared module-scope helper. Hand-rolled response
bodies at every callsite are how parity drift creeps
in — the next refactor updates one branch and forgets
another.

Failure mode (Per Huntova engineering review on
shared-helper interface guarantees):

BRAIN-124 (a493) and BRAIN-125 (a494) made the four
wizard write-rejection responses match: each one
returns `ok:false`, `answers_applied:false`,
`wizard_revision`, `wizard_epoch`, and an explicit
"your answers were not saved" message. The 410
wizard_reset, 409 stale_revision (in
api_wizard_complete), 409 dna_in_flight, and 409
stale_revision (in api_wizard_save_progress) all
have the same semantic payload — and right now each
one is HAND-ROLLED at the callsite.

That means the next refactor that updates one branch
of complete (e.g. adding a new field or renaming
`error_kind`) will silently drift away from save-
progress. In optimistic-concurrency systems, the
client's recovery logic depends on a stable conflict
shape across endpoints. Drift is the failure mode.

Per Huntova engineering review on shared-helper
interface guarantees: every wizard conflict caused
by concurrent state change must be constructed by
ONE shared helper. complete, save-progress, and any
future mutating wizard route should not hand-roll
their own stale/conflict bodies once the public
contract is established.

Invariants:
- Module-scope helper
  `_wizard_conflict_response(kind, current_revision=0,
  current_epoch=0, in_flight_started_at="")` returns
  a JSONResponse with the documented contract:
  - `ok: false`
  - `answers_applied: false`
  - `error_kind: <kind>`
  - `error: <kind-specific message>`
  - `wizard_revision: int`
  - `wizard_epoch: int`
  - For `dna_in_flight`: also `in_flight: true` and
    `in_flight_started_at: str`.
  - For `stale_revision`: also `stale: true`.
- Status codes:
  - `wizard_reset` → 410
  - `stale_revision` → 409
  - `dna_in_flight` → 409
- All four callsites
  (api_wizard_complete: 3, api_wizard_save_progress: 1)
  call the helper instead of building JSONResponse
  inline.
"""
from __future__ import annotations
import inspect
import json


def test_wizard_conflict_response_helper_exists():
    """Module-scope helper exists."""
    import server as _s
    fn = getattr(_s, "_wizard_conflict_response", None)
    assert fn is not None and callable(fn), (
        "BRAIN-126 regression: server must expose "
        "`_wizard_conflict_response(kind, ...)` as a "
        "module-scope helper. Hand-rolled bodies at "
        "every callsite drift over time."
    )


def test_helper_wizard_reset_returns_410():
    """`wizard_reset` → 410 with the documented contract."""
    import server as _s
    resp = _s._wizard_conflict_response(
        "wizard_reset", current_revision=5, current_epoch=2
    )
    assert resp.status_code == 410
    body = json.loads(resp.body)
    assert body.get("ok") is False
    assert body.get("answers_applied") is False
    assert body.get("error_kind") == "wizard_reset"
    assert body.get("wizard_revision") == 5
    assert body.get("wizard_epoch") == 2
    msg = body.get("error", "")
    assert "not saved" in msg.lower() or "not applied" in msg.lower(), (
        "BRAIN-126 regression: wizard_reset error message "
        "must explicitly say the user's answers were not "
        "saved."
    )


def test_helper_stale_revision_returns_409():
    """`stale_revision` → 409 with the documented contract."""
    import server as _s
    resp = _s._wizard_conflict_response(
        "stale_revision", current_revision=7, current_epoch=1
    )
    assert resp.status_code == 409
    body = json.loads(resp.body)
    assert body.get("answers_applied") is False
    assert body.get("error_kind") == "stale_revision"
    assert body.get("wizard_revision") == 7
    assert body.get("wizard_epoch") == 1
    # Legacy `stale: true` flag preserved for
    # back-compat with existing client code.
    assert body.get("stale") is True


def test_helper_dna_in_flight_returns_409():
    """`dna_in_flight` → 409 with the documented contract +
    in_flight_started_at."""
    import server as _s
    resp = _s._wizard_conflict_response(
        "dna_in_flight",
        current_revision=3, current_epoch=1,
        in_flight_started_at="2026-05-04T01:00:00",
    )
    assert resp.status_code == 409
    body = json.loads(resp.body)
    assert body.get("answers_applied") is False
    assert body.get("error_kind") == "dna_in_flight"
    assert body.get("in_flight") is True
    assert body.get("in_flight_started_at") == "2026-05-04T01:00:00"


def test_helper_unknown_kind_falls_back_safely():
    """Defensive: an unknown kind shouldn't crash. Either
    raise a clear error OR fall back to a generic
    conflict response. The choice is up to the impl
    but it must not return None or 200."""
    import server as _s
    try:
        resp = _s._wizard_conflict_response(
            "unknown_kind", current_revision=1
        )
    except (ValueError, KeyError):
        # Acceptable: helper raises on unknown kind.
        return
    # Acceptable: helper returns a 4xx fallback.
    assert 400 <= resp.status_code < 500


def test_complete_callsites_use_helper():
    """Source-level: api_wizard_complete's three
    rejection branches all use the helper instead of
    hand-rolled JSONResponse bodies."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    # Count helper invocations — must be at least 3
    # (one per rejection branch).
    count = src.count("_wizard_conflict_response(")
    assert count >= 3, (
        f"BRAIN-126 regression: api_wizard_complete "
        f"should call `_wizard_conflict_response` at "
        f"least 3 times (wizard_reset + stale_revision "
        f"+ dna_in_flight branches). Found {count}."
    )


def test_save_progress_callsite_uses_helper():
    """Source-level: api_wizard_save_progress's
    BRAIN-68 stale-write 409 must also use the helper."""
    from server import api_wizard_save_progress
    src = inspect.getsource(api_wizard_save_progress)
    assert "_wizard_conflict_response(" in src, (
        "BRAIN-126 regression: api_wizard_save_progress "
        "must use the shared `_wizard_conflict_response` "
        "helper. Pre-fix it had a hand-rolled "
        "stale-revision response with a different shape "
        "(missing `answers_applied`, `error_kind`, "
        "`wizard_epoch`) — that drift is exactly what "
        "the helper extraction prevents."
    )


def test_save_progress_response_has_full_contract():
    """Source-level: confirm the helper-call result on
    save-progress carries the full conflict contract.
    Use the helper output directly — if save-progress
    uses the helper, its 409 response shape is
    guaranteed."""
    import server as _s
    resp = _s._wizard_conflict_response(
        "stale_revision", current_revision=4, current_epoch=0
    )
    body = json.loads(resp.body)
    # All the contract fields BRAIN-125 added must
    # appear via the helper.
    for field in (
        "ok", "answers_applied", "error_kind",
        "wizard_revision", "wizard_epoch", "stale",
    ):
        assert field in body, (
            f"BRAIN-126 regression: helper response "
            f"missing `{field}` — every callsite that "
            f"uses the helper would also miss it."
        )
