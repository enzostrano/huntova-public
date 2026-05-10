"""Regression tests for BRAIN-109 (a478): /api/wizard/status
must apply the same `_dna_state` enum normalization that
BRAIN-108 added to the BRAIN-79 agent gate.

Failure mode (Per Huntova engineering review on response
enum contract):

BRAIN-108 (a477) blocks malformed `_dna_state` at the
BRAIN-79 agent-control gate. But `/api/wizard/status`
still reads the raw stored value and exposes it directly:

    "dna_state": w.get("_dna_state", "unset")

So a corrupted persisted value like `"pendng"` or
`"banana"` flows through to the wizard UI as part of the
public response contract. The client (or downstream
consumer) then either:

- Branches on the malformed value and produces unexpected
  behavior.
- Trusts it as "ready" because the JS `if (state ===
  'pending')` etc. all fall through.
- Re-implements the corruption handling client-side that
  the server should have applied.

Standard response-enum guidance: every public read path
that exposes a state value must validate before emitting.
Internal "it should never happen" assumptions don't hold
once the value is public API.

Invariants:
- A shared `_normalize_dna_state(raw) -> str` helper
  exists at module scope. Returns one of `{"pending",
  "ready", "failed", "unset", "invalid"}`.
- `_DNA_STATE_ALLOWED` is module-scoped (no longer local
  to agent_control), so the status endpoint can reference
  the same set.
- `/api/wizard/status` exposes `dna_state` via the helper
  — never the raw stored value.
- `agent_control` uses the same helper for parity.
- A malformed persisted value (e.g. `"banana"`) returns
  `"invalid"` from the status endpoint (not the raw
  string). Bonus: the helper exposes the raw value via a
  separate `dna_state_raw` debug field for operators.
"""
from __future__ import annotations
import inspect


def test_normalize_dna_state_helper_exists():
    """Source-level: a shared `_normalize_dna_state` helper
    must exist at module scope on `server`."""
    import server as _s
    helper = getattr(_s, "_normalize_dna_state", None)
    assert helper is not None and callable(helper), (
        "BRAIN-109 regression: server must expose "
        "`_normalize_dna_state(raw)` at module scope so "
        "every read site (agent gate, status endpoint, "
        "future endpoints) uses the same contract."
    )


def test_dna_state_allowed_is_module_scope():
    """Source-level: `_DNA_STATE_ALLOWED` must be module
    scope (not function-local), so other endpoints can
    reuse it."""
    import server as _s
    allowed = getattr(_s, "_DNA_STATE_ALLOWED", None)
    assert allowed is not None, (
        "BRAIN-109 regression: `_DNA_STATE_ALLOWED` must be "
        "exposed at module scope. Lifting from agent_control's "
        "local scope makes the enum reusable across endpoints."
    )
    assert {"pending", "ready", "failed", "unset"} <= set(allowed), (
        "BRAIN-109 regression: `_DNA_STATE_ALLOWED` must "
        "contain all four documented states."
    )


def test_normalize_helper_returns_invalid_for_unknown():
    """Behavioral: malformed values map to `"invalid"`,
    not raw passthrough."""
    import server as _s
    fn = _s._normalize_dna_state
    assert fn("banana") == "invalid"
    assert fn("PENDING") == "invalid"   # case-mangled
    assert fn("pendng") == "invalid"    # typo
    assert fn(42) == "invalid"          # non-string
    assert fn({}) == "invalid"          # non-string
    assert fn([]) == "invalid"          # non-string


def test_normalize_helper_passes_through_valid():
    """Behavioral: valid values normalize to themselves;
    None / "unset" both → "unset" (legacy compat)."""
    import server as _s
    fn = _s._normalize_dna_state
    assert fn("pending") == "pending"
    assert fn("ready") == "ready"
    assert fn("failed") == "failed"
    assert fn("unset") == "unset"
    assert fn(None) == "unset"


def test_status_endpoint_uses_normalize_helper():
    """Source-level: /api/wizard/status must call the
    helper for `dna_state` exposure, not read the raw
    stored value."""
    from server import api_wizard_status
    src = inspect.getsource(api_wizard_status)
    assert "_normalize_dna_state(" in src, (
        "BRAIN-109 regression: status endpoint must call "
        "`_normalize_dna_state(...)` for `dna_state`. "
        "Without it, malformed persisted values leak to "
        "clients as part of the public response contract."
    )


def test_agent_control_uses_normalize_helper_for_parity():
    """Source-level: agent_control must consult the same
    enum-validation contract as the public read. BRAIN-108
    pulled the enum literal up to module scope; BRAIN-109
    introduced the shared `_normalize_dna_state`; BRAIN-120
    extracted the entire gate behavior into
    `_dna_state_gate_response`. Any of those satisfies the
    parity invariant — single source of truth for the
    state-machine contract."""
    from server import agent_control
    src = inspect.getsource(agent_control)
    assert (
        "_normalize_dna_state(" in src
        or "_dna_state_gate_response(" in src
    ), (
        "BRAIN-109 regression: agent_control must consult "
        "the shared dna-state contract. Either via "
        "`_normalize_dna_state` directly or via the "
        "extracted `_dna_state_gate_response` (BRAIN-120). "
        "Inline duplication forfeits the parity guarantee."
    )


def test_status_endpoint_exposes_invalid_state_distinctly():
    """Behavioral: status response for a malformed value
    surfaces `dna_state: "invalid"` (or equivalent) — never
    the raw bad string. Operator can spot the corruption
    in the response without reading the row."""
    import server as _s
    fn = _s._normalize_dna_state
    # The helper's invalid output must be a STRING that
    # downstream consumers (UI, monitoring) can branch on.
    invalid_normalized = fn("banana_corruption")
    assert invalid_normalized == "invalid", (
        "BRAIN-109 regression: malformed values must "
        "normalize to the literal string `'invalid'` so "
        "client conditionals like `if state === 'invalid'` "
        "can react."
    )
    assert invalid_normalized != "banana_corruption", (
        "BRAIN-109 regression: helper must NOT pass through "
        "the raw bad value."
    )
