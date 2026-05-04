"""Regression tests for BRAIN-116 (a485): every server
path that reads `_wizard_revision` or `_wizard_epoch`
for a compare-and-swap, stale-write guard, or atomic
flip mutator must use `_safe_nonneg_int` — same hardened
contract BRAIN-115 (a484) applied to the public read.

Failure mode (Per Huntova engineering review on
optimistic concurrency tokens + write-path version
contracts):

BRAIN-115 hardened the public emission of
`_wizard_revision` on /api/wizard/status. But the WRITE
side — the actual conflict-control surface — still
captures the persisted value via the crashy pattern:

    _captured_revision = int(_w_snap.get("_wizard_revision", 0) or 0)
    _cur_rev = int(w.get("_wizard_revision", 0) or 0)

When the persisted value isn't a clean positive int:
- `int("banana")` raises `ValueError`. The handler 500s
  on every save-progress / complete / reset request,
  not the controlled 409 conflict the optimistic-
  concurrency contract is supposed to surface.
- A negative int passes through. The compare-and-swap
  decision (`_cur_rev != _captured_revision`) operates
  on garbage. False accepts (lost updates) or false
  failures (legitimate writes rejected) become possible
  at the moment of mutation.

A concurrency token is part of the WRITE contract, not
just a display field. Read-side hardening without
write-side hardening leaves the actual conflict-control
surface exposed.

Invariants:
- Every site that reads `_wizard_revision` or
  `_wizard_epoch` for a compare-or-write decision uses
  `_safe_nonneg_int`. No remaining `int(... or 0)`
  pattern over those keys in the wizard mutating
  handlers + their flip/merge mutators.
- The legacy `int(... or 0)` pattern is allowed only
  for write-side INCREMENTS where the read-side has
  already been validated upstream within the same
  closure (e.g. inside the same merge mutator after a
  guarded read).
"""
from __future__ import annotations
import inspect
import re

from server import (
    api_wizard_complete,
    api_wizard_save_progress,
    api_wizard_reset,
    admin_wizard_reset,
)


_WIZARD_KEYS = ("_wizard_revision", "_wizard_epoch")


def _crashy_pattern_count(src: str, key: str) -> int:
    """Count occurrences of `int(... <key> ...)` that
    are NOT followed by `_safe_nonneg_int` somewhere on
    the same line — the crashy pattern."""
    # Match: int(<some stuff with key> or 0)
    pattern = rf'\bint\([^)]*{re.escape(key)}[^)]*\)'
    matches = re.findall(pattern, src)
    return len(matches)


def test_api_wizard_complete_write_side_uses_safe_helper():
    """The complete handler captures revision + epoch at
    entry and again inside the BRAIN-88 flip mutator.
    Both must validate the persisted value."""
    src = inspect.getsource(api_wizard_complete)
    for key in _WIZARD_KEYS:
        assert _crashy_pattern_count(src, key) == 0, (
            f"BRAIN-116 regression: api_wizard_complete "
            f"still has unhardened `int(... {key} ...)` "
            f"capture(s). Migrate to _safe_nonneg_int so "
            f"corrupted persisted values produce a "
            f"controlled 409 conflict instead of a 500."
        )
    # Defensive: ensure the helper IS used somewhere in
    # the handler so we know the migration happened
    # (the count==0 above could trivially pass if the
    # handler stopped reading the key — sanity check).
    assert "_safe_nonneg_int(" in src, (
        "BRAIN-116 regression: api_wizard_complete must "
        "actually call _safe_nonneg_int."
    )


def test_api_wizard_save_progress_write_side_uses_safe_helper():
    src = inspect.getsource(api_wizard_save_progress)
    for key in _WIZARD_KEYS:
        assert _crashy_pattern_count(src, key) == 0, (
            f"BRAIN-116 regression: api_wizard_save_progress "
            f"still has unhardened `int(... {key} ...)` "
            f"capture(s). The stale-write guard runs every "
            f"keystroke — a 500 here breaks the wizard form."
        )
    assert "_safe_nonneg_int(" in src


def test_api_wizard_reset_write_side_uses_safe_helper():
    src = inspect.getsource(api_wizard_reset)
    for key in _WIZARD_KEYS:
        assert _crashy_pattern_count(src, key) == 0, (
            f"BRAIN-116 regression: api_wizard_reset still "
            f"has unhardened `int(... {key} ...)`. The reset "
            f"mutator reads epoch to bump it; if persisted "
            f"epoch is corrupted, the entire reset flow 500s "
            f"and the user can't recover."
        )
    assert "_safe_nonneg_int(" in src


def test_admin_wizard_reset_uses_safe_helper():
    src = inspect.getsource(admin_wizard_reset)
    for key in _WIZARD_KEYS:
        assert _crashy_pattern_count(src, key) == 0, (
            f"BRAIN-116 regression: admin_wizard_reset "
            f"still has unhardened `int(... {key} ...)`. "
            f"The operator escape hatch must work even on "
            f"a corrupted row — that's exactly when admins "
            f"need to reset."
        )
    assert "_safe_nonneg_int(" in src


def test_gen_dna_spawn_epoch_uses_safe_helper():
    """`_dna_spawn_epoch` captures the epoch at spawn time
    so the BRAIN-82 epoch gate inside the DNA mutators
    can compare. A corrupted persisted epoch crashes the
    spawn — the entire complete handler 500s after the
    expensive brain+dossier compute already ran (BYOK
    spend wasted)."""
    src = inspect.getsource(api_wizard_complete)
    # Look for `_dna_spawn_epoch =` and confirm the RHS
    # uses _safe_nonneg_int, not raw int().
    m = re.search(
        r'_dna_spawn_epoch\s*=\s*([^\n]+)',
        src,
    )
    assert m, "_dna_spawn_epoch capture should still exist"
    assignment = m.group(1)
    assert "_safe_nonneg_int(" in assignment, (
        "BRAIN-116 regression: `_dna_spawn_epoch` must be "
        "captured via _safe_nonneg_int. A 500 here wastes "
        "the BYOK brain+dossier compute that already "
        "completed before this line runs."
    )


def test_safe_helper_drives_atomic_compare_and_swap_correctly():
    """Behavioral: when a corrupted `_wizard_revision`
    is persisted, _safe_nonneg_int normalizes to 0. The
    write-path's compare-and-swap then sees `0 != 0`
    is False and proceeds — no 500. (The legitimate
    consequence: a writer that captured the post-fix
    `_wizard_revision=0` reads consistent state. The
    corrupted source row is repaired by the bump on
    next write.)"""
    import server as _s
    fn = _s._safe_nonneg_int
    assert fn("banana") == 0
    captured = fn("banana")
    cur = fn("banana")
    # Compare-and-swap: cur != captured → False → write
    # proceeds rather than 500'ing.
    assert cur == captured
