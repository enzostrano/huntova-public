"""Regression tests for BRAIN-119 (a488): server-side
captures of `_wizard_phase` and `_wizard_cursor` must
bound + monotonicity-validate before driving wizard
state-machine transitions.

Failure mode (Per Huntova engineering review on
state-machine coordinates + persisted-workflow-state
validation):

`_wizard_phase` and `_wizard_cursor` are state-machine
coordinates. They drive transitions in save-progress,
the cursor-write clamp, the BRAIN-87 cursor render
contract, and `/api/wizard/status`. The wizard has a
small known number of questions (≤ ~20 historically),
so persisted phase/cursor values >> ~100 always
indicate corruption.

Pre-fix gaps:

1. **`_monotonic_phase` had no upper bound.** A
   corrupted persisted `_wizard_phase=999999` hit
   `max(999999, incoming) → 999999` forever. The
   wizard "stuck at phase 999999" — every renders
   tries to display question index 999999, the
   front-end bounds-checks against `_BRAIN_QUESTIONS`
   length and falls through to the empty/end state.

2. **Cursor-write capture uses crashy `int(... or 0)`.**
   Save-progress reads `_max_unlocked = int(w.get
   ("_wizard_phase", 0) or 0)` to clamp the cursor
   write. Same BRAIN-115/116 failure mode: a non-numeric
   persisted phase 500s the save handler.

3. **Status endpoint emits `phase` raw.** Adjacent
   counters got `_safe_nonneg_int` in BRAIN-115 but
   `phase` was missed; a corrupted persisted phase
   leaks straight to the client (or 500s if
   non-serializable).

Per state-machine guidance: invalid persisted positions
must not silently become legal execution inputs. Every
read of `_wizard_phase` or `_wizard_cursor` that drives
a transition must normalize to a bounded, known-valid
integer.

Invariants:
- Module-scope constant `_WIZARD_PHASE_MAX` (default
  100, env-overridable). Generous enough that real
  wizards (~20 questions) fit comfortably; tight
  enough that a corrupted 999999 fails closed.
- Helper `_normalize_wizard_phase(raw)` returns an int
  in `[0, _WIZARD_PHASE_MAX]`. Type-safe (delegates to
  `_safe_nonneg_int`) AND bound-clamped.
- `_monotonic_phase` clamps its result to
  `[0, _WIZARD_PHASE_MAX]`. Out-of-range persisted
  values self-repair on the next monotonic write.
- Save-progress cursor-clamp capture uses the helper
  (no `int(... or 0)`).
- Status endpoint emits `phase` via the helper for
  parity with `wizard_cursor` (BRAIN-115).
"""
from __future__ import annotations
import inspect


def test_wizard_phase_max_constant_exists():
    """Module-scope upper-bound constant."""
    import server as _s
    val = getattr(_s, "_WIZARD_PHASE_MAX", None)
    assert val is not None, (
        "BRAIN-119 regression: server must expose "
        "`_WIZARD_PHASE_MAX` so phase/cursor reads can "
        "clamp out-of-range persisted values."
    )
    assert isinstance(val, int) and val > 0
    # Sanity: real wizards have ~20 phases; a max of 100
    # is generous. If it's < 30, legitimate flows might
    # bump it.
    assert 30 <= val <= 1000


def test_normalize_wizard_phase_helper_exists():
    """Module-scope helper does the bound + type check."""
    import server as _s
    fn = getattr(_s, "_normalize_wizard_phase", None)
    assert fn is not None and callable(fn), (
        "BRAIN-119 regression: server must expose "
        "`_normalize_wizard_phase(raw)` returning an "
        "int in [0, _WIZARD_PHASE_MAX]."
    )


def test_normalize_phase_clamps_oversized_to_max():
    """A corrupted persisted phase well above the cap
    must not leak through to render or transition
    logic."""
    import server as _s
    cap = _s._WIZARD_PHASE_MAX
    assert _s._normalize_wizard_phase(999999) == cap
    assert _s._normalize_wizard_phase(cap + 1) == cap
    assert _s._normalize_wizard_phase(cap) == cap  # at cap is OK


def test_normalize_phase_clamps_negatives_to_zero():
    """Negative phase = corruption. Clamp to 0."""
    import server as _s
    assert _s._normalize_wizard_phase(-1) == 0
    assert _s._normalize_wizard_phase(-100) == 0


def test_normalize_phase_handles_corrupt_strings():
    """Non-numeric raw → 0. No raise."""
    import server as _s
    assert _s._normalize_wizard_phase("banana") == 0
    assert _s._normalize_wizard_phase(None) == 0
    assert _s._normalize_wizard_phase([]) == 0


def test_normalize_phase_passes_clean_values():
    """Real values flow through."""
    import server as _s
    assert _s._normalize_wizard_phase(0) == 0
    assert _s._normalize_wizard_phase(5) == 5
    assert _s._normalize_wizard_phase(20) == 20


def test_monotonic_phase_clamps_oversized_persisted_value():
    """The BRAIN-3 monotonic guarantee must not let an
    oversized persisted value persist forever. If
    `prev` is 999999 (corruption), the result must be
    clamped — otherwise legitimate `incoming` writes get
    stuck at 999999 forever."""
    import server as _s
    cap = _s._WIZARD_PHASE_MAX
    # prev=999999 (corruption), incoming=5 (legitimate)
    # → result must be at most cap.
    out = _s._monotonic_phase(999999, 5)
    assert out <= cap, (
        "BRAIN-119 regression: _monotonic_phase must clamp "
        "to _WIZARD_PHASE_MAX. Without the clamp, a "
        "corrupted persisted phase blocks every "
        "subsequent write because max(999999, anything "
        "<= cap) = 999999 forever."
    )


def test_monotonic_phase_preserves_monotonicity_within_bounds():
    """Within-bounds values still respect max(prev,
    incoming) so a stale tab can't regress phase."""
    import server as _s
    assert _s._monotonic_phase(5, 3) == 5
    assert _s._monotonic_phase(3, 5) == 5
    assert _s._monotonic_phase(0, 0) == 0


def test_save_progress_cursor_clamp_uses_safe_helper():
    """Source-level: the cursor-clamp capture
    `_max_unlocked` must NOT use the crashy
    `int(... or 0)` pattern. A corrupted persisted phase
    here 500s save-progress on every keystroke."""
    from server import api_wizard_save_progress
    src = inspect.getsource(api_wizard_save_progress)
    # Find _max_unlocked assignment specifically.
    import re
    m = re.search(r'_max_unlocked\s*=\s*([^\n]+)', src)
    assert m, "_max_unlocked capture should still exist"
    assignment = m.group(1)
    assert (
        "_normalize_wizard_phase(" in assignment
        or "_safe_nonneg_int(" in assignment
    ), (
        "BRAIN-119 regression: cursor-clamp capture must "
        "use a bound/safe helper. Crashy `int(... or 0)` "
        "blocks legitimate save-progress on a corrupted "
        "row."
    )


def test_status_endpoint_emits_phase_via_safe_helper():
    """Source-level: /api/wizard/status emits `phase`
    via a safe helper (parity with `wizard_cursor` from
    BRAIN-115). Pre-fix it was emitted via raw
    `w.get('_wizard_phase', 0)` which leaks any persisted
    type to the client."""
    from server import api_wizard_status
    src = inspect.getsource(api_wizard_status)
    import re
    # Match the `phase` emission specifically. Both the
    # public read and the legacy w.get path.
    m = re.search(r'"phase"\s*:\s*([^,\n]+)', src)
    assert m, "`phase` should still be emitted"
    expression = m.group(1)
    assert (
        "_normalize_wizard_phase(" in expression
        or "_safe_nonneg_int(" in expression
    ), (
        "BRAIN-119 regression: status `phase` must be "
        "emitted via _normalize_wizard_phase or "
        "_safe_nonneg_int — same response-validation "
        "rationale as BRAIN-115/109."
    )
