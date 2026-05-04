"""Regression tests for BRAIN-73 (a434): wizard `_wizard_answers`
must conform to a narrow shape contract at write time.

Failure mode (per GPT-5.4 untrusted-JSON-shape audit):

`_merge_wizard_answers` (`server.py:7529+`) blindly merged
`{**prev, **incoming}` with no per-key shape check. The
save-progress mutator's `_DIRECT_FIELDS` loop also accepted any
truthy value:

    for _f in _DIRECT_FIELDS:
        v = answers.get(_f)
        if v not in (None, "", []):
            w[_f] = v

So a client (buggy, malicious, or desync'd) sending a payload
like:

    {"answers": {
        "company_name": {"evil": "nested"},
        "regions": [["nested", "list"], "Italy"],
        "business_description": 12345,
        "buyer_roles": "should-be-list-not-string",
    }}

would persist these malformed shapes into `_wizard_answers`.
Downstream consumers — brain build, dossier generator, fallback
query generator, AI prompt assembly — would then hit
`AttributeError: 'dict' object has no attribute 'lower'`,
silently coerce via `str(...)` (showing the user
`{'evil': 'nested'}` in their assist context), or skip the field
because of an isinstance check. None of these are honest
failures — they all silently degrade behavior.

External JSON from the client is untrusted input. The standard
defense is **schema validation at the boundary**: reject or
coerce unknown shapes BEFORE persisting, not after.

Invariants per field type:
- Scalar string fields (company_name, business_description,
  target_clients, outreach_tone, etc.): must be `str`. Reject
  dict/list/None. Trim + cap length.
- List-of-string fields (regions, services, buyer_roles,
  exclusions, lookalikes, etc.): must be `list` of strings.
  Reject dict. Filter non-strings. Cap list length.
- The merge helper's "empty incoming = no-op" semantics
  (BRAIN-6) must be preserved.
- Unknown keys (no schema entry) are dropped — the wizard has a
  closed set of fields. An attacker can't smuggle arbitrary
  blobs into the user's settings via this endpoint.
"""
from __future__ import annotations
import inspect


def test_save_progress_defines_a_field_schema():
    """Source-level: save-progress (or its merge helper) must
    define an explicit per-field type schema. Without one, every
    downstream consumer has to defend itself, which is the bug
    GPT-5.4 flagged."""
    from server import _merge_wizard_answers
    src = inspect.getsource(_merge_wizard_answers)
    # The schema can live on the helper directly OR be referenced
    # from a module-level constant. Either way, an unambiguous
    # reference must exist.
    has_schema = (
        "_WIZARD_FIELD_SCHEMA" in src
        or "_WIZARD_ANSWER_SCHEMA" in src
        or "_FIELD_TYPES" in src
        or "_validate_wizard_answer" in src
        or "_coerce_wizard_answer" in src
    )
    assert has_schema, (
        "BRAIN-73 regression: _merge_wizard_answers must "
        "validate each incoming answer against a per-field "
        "shape schema. Pre-fix, any dict/list/scalar shape was "
        "merged blindly — downstream code crashed or silently "
        "coerced. Define the schema once at the boundary."
    )


def test_merge_rejects_dict_value_for_scalar_field():
    """The merge helper must reject nested dict values for
    fields declared as scalar strings (company_name, etc.).
    Behavioral test: a hostile payload doesn't pollute prev."""
    from server import _merge_wizard_answers
    prev = {"company_name": "Acme Corp", "regions": ["Italy"]}
    bad = {"company_name": {"evil": "nested-dict"}}
    out = _merge_wizard_answers(prev, bad)
    # Either rejected (kept old value) or coerced to safe string.
    cn = out.get("company_name")
    assert isinstance(cn, str), (
        "BRAIN-73 regression: a dict-shaped payload for "
        "company_name was persisted as a dict. Downstream code "
        f"(`.lower()`, `.strip()`) would crash. Got: {type(cn).__name__}={cn!r}"
    )
    # Must NOT have leaked the {'evil': 'nested-dict'} shape.
    assert "evil" not in str(cn).lower() or cn == "Acme Corp", (
        "BRAIN-73 regression: dict payload's keys leaked into "
        "the persisted scalar value via str() coercion. Reject "
        "or fall back to prev value cleanly."
    )


def test_merge_rejects_nested_list_for_list_field():
    """List-of-string fields (regions, services, buyer_roles)
    must filter out non-string elements. A nested list payload
    must not persist as a list-of-lists."""
    from server import _merge_wizard_answers
    prev = {"regions": ["Italy"]}
    bad = {"regions": [["nested", "list"], "Spain", {"x": 1}, 42]}
    out = _merge_wizard_answers(prev, bad)
    regions = out.get("regions")
    assert isinstance(regions, list), (
        "BRAIN-73 regression: regions must remain a list."
    )
    for item in regions:
        assert isinstance(item, str), (
            f"BRAIN-73 regression: regions list contained a "
            f"non-string element after merge: {type(item).__name__}={item!r}. "
            "Filter non-strings during shape validation."
        )


def test_merge_drops_unknown_keys():
    """The wizard has a closed schema. An incoming payload with
    keys not in the schema must be dropped, not merged. This
    prevents an attacker from smuggling arbitrary blobs into
    user_settings via save-progress."""
    from server import _merge_wizard_answers
    prev = {"company_name": "Acme"}
    incoming = {
        "company_name": "Acme V2",  # legitimate
        "_internal_admin_flag": True,  # smuggled
        "arbitrary_blob": {"a": 1},  # smuggled
        "__proto__": {"polluted": True},  # JS-style prototype-pollution attempt
    }
    out = _merge_wizard_answers(prev, incoming)
    # Legitimate update flowed through.
    assert out.get("company_name") == "Acme V2"
    # Smuggled keys did NOT.
    for bad_key in ("_internal_admin_flag", "arbitrary_blob", "__proto__"):
        assert bad_key not in out, (
            f"BRAIN-73 regression: unknown key '{bad_key}' was "
            "merged into _wizard_answers. The schema is closed; "
            "drop unknown keys at the boundary."
        )


def test_merge_caps_string_field_length():
    """A 1MB string in any scalar field must not persist
    verbatim. The wizard fields all have prompt-budget caps
    (BRAIN-11/13) but those run at PROMPT-ASSEMBLY time;
    persisting unbounded blobs in user_settings.data still
    bloats the row, slows JSON parse on every read, and stresses
    the SQLite WAL."""
    from server import _merge_wizard_answers
    prev = {}
    huge = "X" * 200_000  # 200KB
    bad = {"business_description": huge}
    out = _merge_wizard_answers(prev, bad)
    desc = out.get("business_description") or ""
    assert isinstance(desc, str)
    # Cap should be in the 5k-50k range (generous for real input,
    # tight against blob-bloat). 200KB persisted is the bug.
    assert len(desc) < 100_000, (
        f"BRAIN-73 regression: 200KB payload persisted to "
        f"business_description as {len(desc)} chars. Cap at "
        f"write time so user_settings.data doesn't bloat."
    )


def test_merge_preserves_brain6_empty_payload_noop():
    """Don't regress BRAIN-6: an empty incoming dict must still
    be a no-op (return prev unchanged), not wipe the row."""
    from server import _merge_wizard_answers
    prev = {"company_name": "Acme", "regions": ["Italy"]}
    out = _merge_wizard_answers(prev, {})
    assert out == prev, (
        "BRAIN-73 regression: BRAIN-6 empty-payload no-op "
        "behavior must survive the new shape-validation layer."
    )
    out2 = _merge_wizard_answers(prev, None)
    assert out2 == prev, (
        "BRAIN-73 regression: None incoming must remain a no-op."
    )
