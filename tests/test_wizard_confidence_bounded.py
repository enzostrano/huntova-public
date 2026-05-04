"""Regression tests for BRAIN-135 (a504): the
`/api/wizard/status` public emission of
`_wizard_confidence` must clamp to a documented upper
bound before sending it to clients. Same class as
BRAIN-119 (`_wizard_phase` upper-bound clamp) but for
the sister progress marker.

Failure mode (Per Huntova engineering review on
persisted-workflow-state validation, second-order
follow-up of BRAIN-119):

`_wizard_confidence` is a 0..100 percent progress
marker bumped through `_monotonic_phase` alongside
`_wizard_phase` (both written together at the
save-progress mutator). The pre-BRAIN-135 status
endpoint emitted:

    "confidence": _safe_nonneg_int(w.get("_wizard_confidence")),

`_safe_nonneg_int` only floors negatives; it does not
cap on the upper end. So a corrupted persisted value
of 999999 leaks raw to clients. Concrete failure
modes:

1. **Client-side progress-bar overflow** — the wizard
   UI renders a 0..100 percentage bar; an unbounded
   value blows the layout.
2. **`_monotonic_phase` lock** — exactly the BRAIN-119
   failure mode duplicated for confidence: once a
   garbage 999999 is persisted, every subsequent
   write goes through `_monotonic_phase(prev=999999,
   incoming=clean_value)` which returns 999999. The
   confidence locks at the bogus value forever; every
   real progress signal is silently dropped.
3. **Defense-in-depth gap** — BRAIN-119 already
   covered phase. Confidence shares the exact same
   write path through `_monotonic_phase`. Leaving it
   unbounded is asymmetric defense.

Standard fix mirrors BRAIN-119: every PUBLIC read
of a bounded state-machine progress marker must
clamp to its documented range. A corrupted
persisted value normalizes to the cap (or 0 floor)
so the client gets a usable percentage rather than
garbage.

Invariants:
- Module-scope constant `_WIZARD_CONFIDENCE_MAX`
  exists, env-overridable via
  `HV_WIZARD_CONFIDENCE_MAX`. Default 100 (percent).
- Module-scope helper `_normalize_wizard_confidence`
  clamps to [0, _WIZARD_CONFIDENCE_MAX].
- `/api/wizard/status` emits `confidence` via the
  bounded helper. Source-level proof.
- `_monotonic_phase` already clamps to
  `_WIZARD_PHASE_MAX` on writes — confirm it still
  does so confidence is bounded at both the persist
  and emit boundary.
"""
from __future__ import annotations
import inspect


def test_wizard_confidence_max_constant_exists():
    """Module-scope cap is defined and is a positive int."""
    import server as _s
    cap = getattr(_s, "_WIZARD_CONFIDENCE_MAX", None)
    assert cap is not None, (
        "BRAIN-135 regression: server must expose "
        "`_WIZARD_CONFIDENCE_MAX` constant defining the "
        "documented upper bound for emitted confidence."
    )
    assert isinstance(cap, int) and cap > 0, (
        "_WIZARD_CONFIDENCE_MAX must be a positive int."
    )


def test_wizard_confidence_max_env_overridable():
    """Env override works the same way as
    `_WIZARD_PHASE_MAX` so operators can widen if a
    legitimate confidence scale changes. Source-level
    proof rather than a runtime-reload check —
    reloading `server` mid-test pool wipes the FastAPI
    route table and breaks neighbouring tests."""
    import server as _s
    src = inspect.getsource(_s)
    import re
    m = re.search(
        r'_WIZARD_CONFIDENCE_MAX\s*=\s*int\(\s*os\.environ\.get\(\s*"HV_WIZARD_CONFIDENCE_MAX"\s*\)\s*or\s*"\d+"\s*\)',
        src,
    )
    assert m is not None, (
        "BRAIN-135 regression: `_WIZARD_CONFIDENCE_MAX` "
        "must be defined via "
        "`int(os.environ.get('HV_WIZARD_CONFIDENCE_MAX') "
        "or '<default>')` so operators can widen the cap "
        "without a code edit, parity with "
        "`_WIZARD_PHASE_MAX`."
    )


def test_normalize_wizard_confidence_clamps_corrupted_high_value():
    """The headline failure: a corrupted 999999 persisted
    value must clamp to the cap, not leak raw."""
    import server as _s
    fn = getattr(_s, "_normalize_wizard_confidence", None)
    assert fn is not None and callable(fn), (
        "BRAIN-135 regression: server must expose "
        "`_normalize_wizard_confidence(raw)` for safe "
        "public emission of the confidence progress "
        "marker."
    )
    cap = _s._WIZARD_CONFIDENCE_MAX
    assert fn(999999) == cap
    assert fn(10**9) == cap


def test_normalize_wizard_confidence_passes_clean_values():
    """Clean values within range pass through unchanged."""
    import server as _s
    assert _s._normalize_wizard_confidence(0) == 0
    assert _s._normalize_wizard_confidence(50) == 50
    cap = _s._WIZARD_CONFIDENCE_MAX
    assert _s._normalize_wizard_confidence(cap) == cap


def test_normalize_wizard_confidence_floors_negatives():
    """Negatives clamp to 0 (delegated to
    `_safe_nonneg_int` for type-safety parity)."""
    import server as _s
    assert _s._normalize_wizard_confidence(-1) == 0
    assert _s._normalize_wizard_confidence(-999) == 0


def test_normalize_wizard_confidence_handles_garbage():
    """Strings / dicts / None coerce to 0 (same
    contract as `_safe_nonneg_int`)."""
    import server as _s
    assert _s._normalize_wizard_confidence(None) == 0
    assert _s._normalize_wizard_confidence("banana") == 0
    assert _s._normalize_wizard_confidence([1, 2]) == 0
    assert _s._normalize_wizard_confidence({"x": 1}) == 0


def test_normalize_wizard_confidence_handles_string_ints():
    """JSON-loaded "75" must still work (same shape as
    `_safe_nonneg_int` already supports)."""
    import server as _s
    assert _s._normalize_wizard_confidence("75") == 75
    assert _s._normalize_wizard_confidence("0") == 0


def test_status_endpoint_emits_confidence_via_bounded_helper():
    """Source-level: status endpoint emits `confidence`
    via the bounded helper, not via the unbounded
    `_safe_nonneg_int(... )` pattern."""
    from server import api_wizard_status
    src = inspect.getsource(api_wizard_status)
    import re
    m = re.search(
        r'"confidence"\s*:\s*([^,\n]+)',
        src,
    )
    assert m, (
        "BRAIN-135 regression: status endpoint should "
        "still emit `confidence`."
    )
    expression = m.group(1)
    assert "_normalize_wizard_confidence(" in expression, (
        "BRAIN-135 regression: `confidence` must be "
        "emitted via `_normalize_wizard_confidence(...)` "
        "so a corrupted persisted 999999 normalizes to "
        "the cap rather than leaking raw to the client."
    )


def test_monotonic_phase_still_clamps_confidence_writes():
    """Defense-in-depth: `_monotonic_phase` is the
    write-path for both phase and confidence. It must
    still clamp at write time so corrupted values don't
    persist in the first place. Behavioural smoke test
    rather than source-level — protects the
    write-boundary half of the BRAIN-135 invariant."""
    import server as _s
    cap = max(_s._WIZARD_PHASE_MAX, _s._WIZARD_CONFIDENCE_MAX)
    out = _s._monotonic_phase(prev=999999, incoming=10)
    assert out <= cap, (
        "BRAIN-135 regression: `_monotonic_phase` must "
        "clamp at the write boundary so a corrupted "
        "999999 prev cannot lock confidence forever."
    )
