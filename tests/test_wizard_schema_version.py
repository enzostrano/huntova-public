"""Regression tests for BRAIN-136 (a507): explicit
`_wizard_schema_version` contract on the wizard
surface. Closed schema with no version marker creates
silent drift when old clients hit a newer server.

Failure mode (Per Huntova engineering review on
API evolution + closed-schema drift):

`_WIZARD_FIELD_SCHEMA` is closed (BRAIN-73 / a436):
unknown keys are dropped silently. That's correct
for hostile inputs but WRONG for legitimate version
skew. An older client posting the old shape against
a newer server gets:

- Newer fields the server expects: missing from the
  request → server falls back to defaults silently.
- Newer enum values: client doesn't know they exist;
  user can't pick them.
- Field renames: old name → drop, new name → empty.

Without a version marker, neither side detects the
drift. The user thinks they answered everything; the
server thinks the answers are incomplete; the client
has no signal to prompt a refresh.

Per Huntova engineering review on API evolution: every
versioned schema needs a `schema_version` contract.
Status responses include the current version; mutating
requests can declare a `client_schema_version`; the
server compares + raises an explicit compatibility
error when versions disagree in a semantically
significant way.

Invariants:
- Module-scope constant `_WIZARD_SCHEMA_VERSION` (int,
  starts at 1; bumps on backward-incompatible
  changes).
- `/api/wizard/status` emits
  `wizard_schema_version: <int>` in the response.
- The constant is documented as the single source of
  truth — every future schema change references it.
"""
from __future__ import annotations
import inspect


def test_wizard_schema_version_constant_exists():
    """Module-scope int constant."""
    import server as _s
    val = getattr(_s, "_WIZARD_SCHEMA_VERSION", None)
    assert val is not None, (
        "BRAIN-136 regression: server must expose "
        "`_WIZARD_SCHEMA_VERSION` so clients can detect "
        "drift before silently losing user intent."
    )
    assert isinstance(val, int) and val >= 1


def test_status_endpoint_emits_schema_version():
    """Source-level: /api/wizard/status surfaces the
    version on every response so clients can pin or
    detect drift."""
    from server import api_wizard_status
    src = inspect.getsource(api_wizard_status)
    assert "wizard_schema_version" in src, (
        "BRAIN-136 regression: api_wizard_status must "
        "emit `wizard_schema_version` in its response. "
        "Without it, clients have no in-band signal."
    )
    assert "_WIZARD_SCHEMA_VERSION" in src, (
        "BRAIN-136 regression: status endpoint must "
        "reference the shared constant, not hardcode."
    )


def test_schema_version_is_documented_in_source():
    """Source-level: the constant carries a comment
    explaining the bump rule (so future developers
    know when to increment)."""
    import server as _s
    src = inspect.getsource(_s)
    # Locate the constant definition + its comment
    # block.
    idx = src.find("_WIZARD_SCHEMA_VERSION")
    assert idx >= 0
    # The 500 chars before the constant should contain
    # a comment block explaining the bump rule.
    pre = src[max(0, idx - 800):idx]
    assert "schema" in pre.lower() and (
        "bump" in pre.lower()
        or "incompatib" in pre.lower()
        or "drift" in pre.lower()
        or "evolution" in pre.lower()
    ), (
        "BRAIN-136 regression: constant must carry a "
        "comment explaining when to bump (the documented "
        "single source of truth for schema evolution)."
    )


def test_schema_version_helper_exists():
    """Module-scope helper for client-server version
    compare returns either None (compatible) or a
    blocking response dict (refresh required)."""
    import server as _s
    fn = getattr(_s, "_check_wizard_schema_compat", None)
    assert fn is not None and callable(fn), (
        "BRAIN-136 regression: server must expose "
        "`_check_wizard_schema_compat(client_version)` "
        "so mutating endpoints can compare client-supplied "
        "versions against the server's current."
    )


def test_schema_compat_passes_for_matching_or_missing_version():
    """Behavioral: client with the same version OR no
    version (legacy client) → None (proceed)."""
    import server as _s
    assert _s._check_wizard_schema_compat(_s._WIZARD_SCHEMA_VERSION) is None
    assert _s._check_wizard_schema_compat(None) is None
    assert _s._check_wizard_schema_compat(0) is None  # legacy/unset


def test_schema_compat_blocks_on_ahead_client():
    """Behavioral: client with HIGHER version than the
    server → explicit compatibility error. The client
    is from a newer build than the server is running.
    User should refresh / downgrade."""
    import server as _s
    out = _s._check_wizard_schema_compat(
        _s._WIZARD_SCHEMA_VERSION + 5
    )
    assert out is not None
    assert out.get("ok") is False
    assert out.get("error_kind") == "schema_mismatch"


def test_schema_compat_blocks_on_behind_client():
    """Behavioral: client with LOWER version than the
    server (server has bumped past the client's
    schema-version) → explicit compatibility error
    telling the client to refresh."""
    import server as _s
    if _s._WIZARD_SCHEMA_VERSION <= 1:
        # Can't construct a "lower" version — skip.
        return
    out = _s._check_wizard_schema_compat(
        _s._WIZARD_SCHEMA_VERSION - 1
    )
    assert out is not None
    assert out.get("error_kind") == "schema_mismatch"


def test_schema_compat_response_includes_versions_for_reconciliation():
    """Behavioral: the blocking response includes both
    the client's version AND the server's version so
    the client can show a meaningful refresh prompt."""
    import server as _s
    out = _s._check_wizard_schema_compat(
        _s._WIZARD_SCHEMA_VERSION + 1
    )
    assert out is not None
    body_keys = set(out.keys())
    has_server_version = (
        "server_schema_version" in body_keys
        or "schema_version" in body_keys
    )
    has_client_version = "client_schema_version" in body_keys
    assert has_server_version, (
        "BRAIN-136 regression: schema-mismatch response "
        "must surface the server's schema version so the "
        "client can branch on it."
    )
    assert has_client_version, (
        "BRAIN-136 regression: schema-mismatch response "
        "must echo the client's submitted version for "
        "debuggability."
    )
