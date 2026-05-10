"""Regression tests for BRAIN-115 (a484): the
`/api/wizard/status` public emission of `_wizard_revision`
must validate-and-normalize the persisted value before
sending it to clients. Same class as BRAIN-109 (DNA enum
validation) but for the optimistic-concurrency token.

Failure mode (Per Huntova engineering review on
optimistic-concurrency tokens + standard row-version
guidance):

`_wizard_revision` is the optimistic-concurrency token
(BRAIN-14). Every save-progress / complete request
captures it and the server bumps it on success — clients
detect "stale copy" by comparing their captured revision
to the server's current one. The contract requires the
revision to be a non-negative monotonic integer.

The pre-BRAIN-115 status endpoint emitted:

    "wizard_revision": int(w.get("_wizard_revision", 0) or 0),

This pattern has three concrete failure modes when the
persisted value isn't a clean positive int:

1. **String / list / dict** — `int("banana")` raises
   `ValueError`. The status request 500s. The whole
   wizard UI breaks because every client-side fetch
   throws. (`int(...)` over arbitrary user-influenced
   values is a known footgun.)
2. **Negative integer** — `int(-3) == -3`. The negative
   value flows through to the client, which now uses
   `-3` as its optimistic-concurrency baseline. The
   next save-progress sends `_captured_revision=-3`,
   the server's stale-write guard `_cur_rev != -3` is
   true forever, every save 409s.
3. **Boolean True / floats / etc.** — `int(True) == 1`,
   `int(3.7) == 3`. Silent coercion masks a
   data-quality bug; nobody notices the row is
   corrupted.

Standard fix: every PUBLIC read of an optimistic-
concurrency token must validate-and-normalize. A
corrupted persisted value normalizes to 0 (a valid
floor) so the client gets a usable token rather than
garbage or a 500. Same response-contract pattern as
BRAIN-109's `_normalize_dna_state`.

Invariants:
- Module-scope helper `_safe_nonneg_int(raw, default=0)`
  exists. Returns a non-negative `int` for any input.
  Strings that parse as non-negative ints pass through.
  Floats truncate (max 0). Negatives clamp to 0.
  Anything that can't coerce → `default`.
- `/api/wizard/status` emits `wizard_revision` via the
  helper. Source-level proof.
- Adjacent monotonic counters in the same response
  (`wizard_epoch`, `wizard_cursor`, `train_count`,
  `train_attempts`) also use the helper for parity —
  they're emitted via the same crashy `int(... or 0)`
  pattern and share the same failure modes.
"""
from __future__ import annotations
import inspect


def test_safe_nonneg_int_helper_exists():
    """Module-scope helper exists."""
    import server as _s
    fn = getattr(_s, "_safe_nonneg_int", None)
    assert fn is not None and callable(fn), (
        "BRAIN-115 regression: server must expose "
        "`_safe_nonneg_int(raw, default=0)` for safe "
        "public emission of optimistic-concurrency "
        "tokens and similar monotonic counters."
    )


def test_safe_nonneg_int_passes_clean_ints():
    import server as _s
    assert _s._safe_nonneg_int(0) == 0
    assert _s._safe_nonneg_int(1) == 1
    assert _s._safe_nonneg_int(42) == 42
    assert _s._safe_nonneg_int(99999) == 99999


def test_safe_nonneg_int_clamps_negatives_to_zero():
    """A persisted negative value (operator UPDATE,
    rollback bug) must not flow through as a negative
    optimistic-concurrency token."""
    import server as _s
    assert _s._safe_nonneg_int(-1) == 0
    assert _s._safe_nonneg_int(-100) == 0


def test_safe_nonneg_int_handles_string_ints():
    """JSON-loaded "5" should still work — same shape
    as the existing `int(... or 0)` pattern handled."""
    import server as _s
    assert _s._safe_nonneg_int("0") == 0
    assert _s._safe_nonneg_int("5") == 5
    assert _s._safe_nonneg_int("  17  ") == 17


def test_safe_nonneg_int_corrupt_strings_to_default():
    """A corrupted string must return the default
    (0 by default), NOT raise."""
    import server as _s
    assert _s._safe_nonneg_int("banana") == 0
    assert _s._safe_nonneg_int("not-a-number") == 0
    assert _s._safe_nonneg_int("-3.7banana") == 0


def test_safe_nonneg_int_handles_none_and_empty():
    """None and "" map to default (0)."""
    import server as _s
    assert _s._safe_nonneg_int(None) == 0
    assert _s._safe_nonneg_int("") == 0


def test_safe_nonneg_int_truncates_floats_floors_zero():
    """A float truncates downward (3.7 → 3) and a
    negative float floors to 0."""
    import server as _s
    assert _s._safe_nonneg_int(3.7) == 3
    assert _s._safe_nonneg_int(0.9) == 0
    assert _s._safe_nonneg_int(-0.5) == 0


def test_safe_nonneg_int_handles_list_dict_safely():
    """Containers / unsupported types → default. Must
    not raise (status endpoint can't 500 the whole UI
    just because a row column is corrupt)."""
    import server as _s
    assert _s._safe_nonneg_int([1, 2]) == 0
    assert _s._safe_nonneg_int({"x": 1}) == 0
    assert _s._safe_nonneg_int(object()) == 0


def test_safe_nonneg_int_respects_default():
    """Caller can override the default (e.g. -1 sentinel
    or some legacy-compat default)."""
    import server as _s
    assert _s._safe_nonneg_int("banana", default=-1) == -1


def test_status_endpoint_uses_safe_helper_for_wizard_revision():
    """Source-level: status endpoint emits
    `wizard_revision` via the helper, not via the
    crash-prone `int(... or 0)` pattern."""
    from server import api_wizard_status
    src = inspect.getsource(api_wizard_status)
    # The relevant assignment must reference the helper.
    # Match the wizard_revision key emission specifically.
    import re
    m = re.search(
        r'"wizard_revision"\s*:\s*([^,\n]+)',
        src,
    )
    assert m, (
        "BRAIN-115 regression: status endpoint should "
        "still emit `wizard_revision`."
    )
    expression = m.group(1)
    assert "_safe_nonneg_int(" in expression, (
        "BRAIN-115 regression: `wizard_revision` must be "
        "emitted via `_safe_nonneg_int(...)` so a "
        "corrupted persisted value normalizes to 0 rather "
        "than 500'ing the request or leaking a negative."
    )


def test_status_endpoint_uses_safe_helper_for_wizard_epoch():
    """Parity: `wizard_epoch` is the same kind of
    monotonic counter and shares the failure modes."""
    from server import api_wizard_status
    src = inspect.getsource(api_wizard_status)
    import re
    m = re.search(r'"wizard_epoch"\s*:\s*([^,\n]+)', src)
    assert m, "wizard_epoch should still be emitted"
    assert "_safe_nonneg_int(" in m.group(1), (
        "BRAIN-115 regression: `wizard_epoch` must be "
        "emitted via the same helper for parity (same "
        "crashy `int(... or 0)` failure modes)."
    )


def test_status_endpoint_uses_safe_helper_for_wizard_cursor():
    """Parity: `wizard_cursor` is also a non-negative
    counter exposed via the same crashy pattern."""
    from server import api_wizard_status
    src = inspect.getsource(api_wizard_status)
    import re
    m = re.search(r'"wizard_cursor"\s*:\s*([^,\n]+)', src)
    assert m, "wizard_cursor should still be emitted"
    assert "_safe_nonneg_int(" in m.group(1), (
        "BRAIN-115 regression: `wizard_cursor` must be "
        "emitted via the same helper for parity."
    )


def test_status_endpoint_uses_safe_helper_for_train_counters():
    """Parity: `train_count` and `train_attempts` are
    audit counters — emitting -1 or 500'ing the
    request would break the operator dashboard."""
    from server import api_wizard_status
    src = inspect.getsource(api_wizard_status)
    import re
    for key in ("train_count", "train_attempts"):
        m = re.search(rf'"{key}"\s*:\s*([^,\n]+)', src)
        if m:
            assert "_safe_nonneg_int(" in m.group(1), (
                f"BRAIN-115 regression: `{key}` must be "
                f"emitted via `_safe_nonneg_int` for parity."
            )
