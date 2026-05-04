"""Regression tests for BRAIN-140 (a521): /api/setup/key
+ /api/settings must enforce the same body-byte cap as
the BRAIN-117/118/122 wizard + agent surface. Adjacent
mutating endpoints with `request.json()` are the
remaining oversize-body ingress vectors.

Failure mode (Per Huntova engineering review on
endpoint-specific request-size limits):

BRAIN-117 (a486) capped /api/wizard/save-progress +
complete. BRAIN-118 (a487) extended to scan + phase5 +
assist. BRAIN-122 (a491) extended to /agent/control.
The settings POST surface (`/api/setup/key` for first-
run keychain writes; `/api/settings` for general
settings POST) accepted arbitrary-sized bodies
before parse — the same OWASP API4:2023 unrestricted
resource consumption gap.

Per Huntova engineering review: every mutating endpoint
that accepts user-supplied JSON and can trigger
meaningful server work must enforce the same top-level
body byte cap before `request.json()` runs.

Invariants:
- `api_setup_key` calls `_enforce_body_byte_cap(request,
  _WIZARD_BODY_BYTES_MAX)` BEFORE `request.json()`.
- `api_save_settings` calls the same helper before
  `request.json()`.
- Both use the shared `_WIZARD_BODY_BYTES_MAX` constant
  for tunability parity with the wizard surface.
"""
from __future__ import annotations
import inspect


def test_setup_key_enforces_byte_cap():
    """Source-level: api_setup_key calls the helper."""
    from server import api_setup_key
    src = inspect.getsource(api_setup_key)
    assert "_enforce_body_byte_cap(" in src, (
        "BRAIN-140 regression: api_setup_key must call "
        "`_enforce_body_byte_cap`. Without it, /api/setup/"
        "key is an oversize-body ingress vector — same "
        "class as BRAIN-117/118 closed for wizard."
    )


def test_setup_key_byte_cap_precedes_json_parse():
    """Source-level: byte-cap precedes `request.json()`."""
    from server import api_setup_key
    src = inspect.getsource(api_setup_key)
    cap_idx = src.find("_enforce_body_byte_cap(")
    json_idx = src.find("request.json(")
    assert cap_idx >= 0 and json_idx >= 0
    assert cap_idx < json_idx, (
        "BRAIN-140 regression: byte-cap must precede "
        "`request.json()` in api_setup_key — oversize "
        "body must reject without parse cost."
    )


def test_setup_key_uses_shared_constant():
    """Source-level: uses the shared constant for
    tunability parity."""
    from server import api_setup_key
    src = inspect.getsource(api_setup_key)
    assert "_WIZARD_BODY_BYTES_MAX" in src, (
        "BRAIN-140 regression: api_setup_key must "
        "reference the shared `_WIZARD_BODY_BYTES_MAX` "
        "constant for tunability parity with the wizard "
        "surface."
    )


def test_settings_enforces_byte_cap():
    """Source-level: api_save_settings calls the helper."""
    from server import api_save_settings
    src = inspect.getsource(api_save_settings)
    assert "_enforce_body_byte_cap(" in src, (
        "BRAIN-140 regression: api_save_settings must "
        "call `_enforce_body_byte_cap`."
    )


def test_settings_byte_cap_precedes_json_parse():
    """Source-level: byte-cap precedes `request.json()`."""
    from server import api_save_settings
    src = inspect.getsource(api_save_settings)
    cap_idx = src.find("_enforce_body_byte_cap(")
    json_idx = src.find("request.json(")
    assert cap_idx >= 0 and json_idx >= 0
    assert cap_idx < json_idx, (
        "BRAIN-140 regression: byte-cap must precede "
        "`request.json()` in api_save_settings."
    )


def test_settings_uses_shared_constant():
    """Source-level: uses the shared constant."""
    from server import api_save_settings
    src = inspect.getsource(api_save_settings)
    assert "_WIZARD_BODY_BYTES_MAX" in src, (
        "BRAIN-140 regression: api_save_settings must "
        "reference the shared `_WIZARD_BODY_BYTES_MAX` "
        "constant."
    )
