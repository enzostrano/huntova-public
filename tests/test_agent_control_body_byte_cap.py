"""Regression tests for BRAIN-122 (a491): `/agent/control`
must enforce the same body-byte cap as the wizard
mutating endpoints. Inconsistent posture across adjacent
public entry points is itself a bug — the unbounded
control endpoint becomes the new easiest resource-
exhaustion seam.

Failure mode (Per Huntova engineering review on
endpoint-specific request-size limits):

BRAIN-117 (a486) capped `/api/wizard/save-progress` and
`/api/wizard/complete`. BRAIN-118 (a487) extended the
cap to `/api/wizard/scan`, `/api/wizard/generate-phase5`,
`/api/wizard/assist`. After both releases, every wizard
mutating route rejects oversize bodies before
`request.json()` runs.

`/agent/control` was missed. It accepts a JSON body with
`action` (string), `countries` (list), and config fields
(int / str). Real payloads are tiny — a country list at
worst is ~30 entries × ~50 bytes. A `_WIZARD_BODY_BYTES_MAX`
cap (256 KiB default) would never trip a legitimate
client. But without the cap, a malicious or buggy client
can post a 10 MB body that gets fully buffered and
parsed before any agent dispatch. That undoes the
resource-hardening posture: the unbounded entry point
becomes the easiest exhaustion seam.

Per Huntova engineering review on endpoint-specific
request-size limits + the BRAIN-117/118 invariant: every
wizard or agent endpoint that accepts client JSON and
can trigger meaningful server work must enforce the same
top-level body byte cap before `request.json()` runs.

Invariants:
- `/agent/control` invokes
  `_enforce_body_byte_cap(request, _WIZARD_BODY_BYTES_MAX)`
  BEFORE `request.json()`.
- The cap is the same shared `_WIZARD_BODY_BYTES_MAX`
  constant — operators tuning the cap change one place,
  not two.
- The 413 response shape matches the wizard endpoints
  (`{ok:false, error_kind:"payload_too_large",
  max_bytes:N}`) — predictable for clients that already
  branch on it.
"""
from __future__ import annotations
import inspect


def test_agent_control_enforces_byte_cap():
    """Source-level: agent_control calls the helper."""
    from server import agent_control
    src = inspect.getsource(agent_control)
    assert "_enforce_body_byte_cap(" in src, (
        "BRAIN-122 regression: agent_control must call "
        "`_enforce_body_byte_cap`. Without it, /agent/control "
        "is the new easiest resource-exhaustion seam — a "
        "client can post a 10 MB body and force full parse "
        "before any agent dispatch decision."
    )


def test_agent_control_byte_cap_precedes_json_parse():
    """Source-level: byte-cap precedes `request.json()` so
    an oversize body short-circuits without parse cost."""
    from server import agent_control
    src = inspect.getsource(agent_control)
    cap_idx = src.find("_enforce_body_byte_cap(")
    json_idx = src.find("request.json(")
    assert cap_idx >= 0 and json_idx >= 0
    assert cap_idx < json_idx, (
        "BRAIN-122 regression: byte-cap must precede "
        "`request.json()` in agent_control."
    )


def test_agent_control_uses_shared_constant():
    """Source-level: agent_control uses the shared
    `_WIZARD_BODY_BYTES_MAX` constant. The constant is
    operator-tunable; using a hardcoded literal here
    breaks tunability parity across the request surface."""
    from server import agent_control
    src = inspect.getsource(agent_control)
    assert "_WIZARD_BODY_BYTES_MAX" in src, (
        "BRAIN-122 regression: agent_control must reference "
        "the shared `_WIZARD_BODY_BYTES_MAX` constant. "
        "Operators tuning the cap should change one place; "
        "drift across endpoints is a maintenance hazard."
    )


def test_byte_cap_call_precedes_dna_gate_check():
    """Source-level: the byte-cap check should run
    BEFORE the BRAIN-120 dna gate. The gate fetches
    settings (DB read); the body-byte cap is cheaper
    (header check) and rejects clearly malformed
    requests before any DB work. Order: byte-cap →
    json parse → dna gate → agent dispatch."""
    from server import agent_control
    src = inspect.getsource(agent_control)
    cap_idx = src.find("_enforce_body_byte_cap(")
    gate_idx = src.find("_dna_state_gate_response(")
    assert cap_idx >= 0
    assert gate_idx >= 0
    assert cap_idx < gate_idx, (
        "BRAIN-122 regression: byte-cap must precede the "
        "dna-state gate so an oversize body never reaches "
        "the more expensive precondition check."
    )
