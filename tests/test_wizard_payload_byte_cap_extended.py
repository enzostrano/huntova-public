"""Regression tests for BRAIN-118 (a487): extend the
BRAIN-117 byte cap to the remaining wizard mutating
endpoints — `/api/wizard/assist`, `/api/wizard/scan`,
`/api/wizard/generate-phase5`. Inconsistent posture
(only some endpoints capped) is itself a bug.

Failure mode (Per Huntova engineering review on
endpoint-specific request-size limits):

BRAIN-117 (a486) capped `/api/wizard/save-progress`
and `/api/wizard/complete`. The other three wizard
mutating endpoints still accept arbitrary-sized
bodies before parsing:

- `/api/wizard/assist` — chat refinement endpoint.
  Accepts free-text `message`, `question_context`,
  `current_answer`, plus a `history` list of
  conversation turns. The most obvious oversized-paste
  vector in the wizard surface — users paste
  transcripts, blog posts, marketing copy directly
  into the textarea.
- `/api/wizard/scan` — accepts a `url` field. The
  field itself is small but a malicious client can
  attach arbitrary other JSON keys at the top level;
  the `request.json()` call still parses the whole
  body before the URL is extracted.
- `/api/wizard/generate-phase5` — accepts `answers` +
  `scanData` (the raw scan_report from the prior scan
  step). scanData can be large.

All three trigger BYOK spend on the AI provider for
each accepted call. Capping body size BEFORE parse +
BEFORE the rate-limit decision lets oversize attempts
short-circuit cheaply.

Per Huntova engineering review on endpoint-specific
request-size limits + the BRAIN-117 invariant: every
wizard endpoint accepting free-text or user-supplied
JSON that can trigger model work must enforce the
byte cap before parsing.

Invariants:
- `api_wizard_assist`, `api_wizard_scan`, and
  `api_wizard_generate_phase5` each call
  `_enforce_body_byte_cap(request,
  _WIZARD_BODY_BYTES_MAX)` BEFORE `request.json()`.
- The cap call sits in the existing call order such
  that rate-check still runs first (cheap denial),
  then byte-cap (no parse), then daily-quota check
  (cheap), then json parse, then expensive work. This
  preserves the BRAIN-117 ordering principle.
"""
from __future__ import annotations
import inspect


def test_assist_enforces_byte_cap():
    """Source-level: api_wizard_assist calls the helper
    BEFORE any json parsing. Highest-risk path:
    free-text input."""
    from server import api_wizard_assist
    src = inspect.getsource(api_wizard_assist)
    assert "_enforce_body_byte_cap(" in src, (
        "BRAIN-118 regression: api_wizard_assist must "
        "call `_enforce_body_byte_cap`. Free-text "
        "endpoints are the easiest oversized-paste "
        "vector — leaving assist uncapped is the "
        "highest-risk version of the same OWASP-API4 "
        "hole BRAIN-117 closed for save-progress + "
        "complete."
    )


def test_assist_byte_cap_precedes_json_parse():
    """The byte-cap check must come BEFORE
    `request.json()` so an oversize body never pays
    parse cost."""
    from server import api_wizard_assist
    src = inspect.getsource(api_wizard_assist)
    cap_idx = src.find("_enforce_body_byte_cap(")
    json_idx = src.find("request.json(")
    assert cap_idx >= 0 and json_idx >= 0
    assert cap_idx < json_idx, (
        "BRAIN-118 regression: byte-cap must precede "
        "request.json() in api_wizard_assist."
    )


def test_scan_enforces_byte_cap():
    """Source-level: api_wizard_scan also enforces the
    cap. Even though scan's expected payload is small
    (just a URL), `request.json()` parses the entire
    body before the URL is extracted."""
    from server import api_wizard_scan
    src = inspect.getsource(api_wizard_scan)
    assert "_enforce_body_byte_cap(" in src, (
        "BRAIN-118 regression: api_wizard_scan must "
        "call `_enforce_body_byte_cap` for consistent "
        "endpoint-specific posture."
    )


def test_scan_byte_cap_precedes_json_parse():
    from server import api_wizard_scan
    src = inspect.getsource(api_wizard_scan)
    cap_idx = src.find("_enforce_body_byte_cap(")
    json_idx = src.find("request.json(")
    assert cap_idx >= 0 and json_idx >= 0
    assert cap_idx < json_idx, (
        "BRAIN-118 regression: byte-cap must precede "
        "request.json() in api_wizard_scan."
    )


def test_phase5_enforces_byte_cap():
    """Source-level: api_wizard_generate_phase5
    enforces the cap. scanData can be large."""
    from server import api_wizard_generate_phase5
    src = inspect.getsource(api_wizard_generate_phase5)
    assert "_enforce_body_byte_cap(" in src, (
        "BRAIN-118 regression: api_wizard_generate_phase5 "
        "must call `_enforce_body_byte_cap`."
    )


def test_phase5_byte_cap_precedes_json_parse():
    from server import api_wizard_generate_phase5
    src = inspect.getsource(api_wizard_generate_phase5)
    cap_idx = src.find("_enforce_body_byte_cap(")
    json_idx = src.find("request.json(")
    assert cap_idx >= 0 and json_idx >= 0
    assert cap_idx < json_idx, (
        "BRAIN-118 regression: byte-cap must precede "
        "request.json() in api_wizard_generate_phase5."
    )


def test_all_wizard_mutating_endpoints_share_byte_cap_constant():
    """All five wizard mutating endpoints reference the
    same `_WIZARD_BODY_BYTES_MAX` constant. Operators
    tuning the cap should change one place, not five.
    """
    from server import (
        api_wizard_save_progress,
        api_wizard_complete,
        api_wizard_assist,
        api_wizard_scan,
        api_wizard_generate_phase5,
    )
    for fn in (
        api_wizard_save_progress,
        api_wizard_complete,
        api_wizard_assist,
        api_wizard_scan,
        api_wizard_generate_phase5,
    ):
        src = inspect.getsource(fn)
        assert "_WIZARD_BODY_BYTES_MAX" in src, (
            f"BRAIN-118 regression: {fn.__name__} must "
            f"reference the shared `_WIZARD_BODY_BYTES_MAX` "
            f"constant for tunability parity."
        )
