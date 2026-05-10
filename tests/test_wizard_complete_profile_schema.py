"""Regression tests for BRAIN-75 (a436): /api/wizard/complete's
`profile` payload from the client must pass the same closed-schema
contract that BRAIN-73 added for `_wizard_answers`.

Failure mode (per GPT-5.4 closed-schema-at-boundary audit):

`_apply_wizard_mutations` (`server.py:8064+`) writes profile
fields directly to the wizard blob:

    for k, v in profile.items():
        if v is None: continue
        if k in ("_interview_complete",): continue
        if k in _PROTECTED_KEYS: continue
        w[k] = v

Pre-fix, this was the LAST unguarded path from untrusted client
input to stored wizard state. BRAIN-73 closed the
save-progress side; BRAIN-74 closed the scan-output side; this
endpoint was the third boundary that needed the same contract.

A client (buggy / malicious / desync'd) can post:

    POST /api/wizard/complete
    {"profile": {
        "company_name": {"evil": "nested-dict"},
        "regions": [["nested-list"], 42, {"x":1}, "Italy"],
        "business_description": "X" * 200_000,
        "_internal_admin_flag": true,
        "__proto__": {"polluted": true},
        "made_up_field": [1,2,3]
    }, "history": []}

→ all of these flow into `w[k] = v` and persist to
`user_settings.data` via the merge_settings mutator. Downstream
brain build, dossier, fallback queries, AI prompts then crash
or silently degrade.

Invariants:
- `_apply_wizard_mutations` (or the endpoint pre-call) must
  validate `profile` against `_WIZARD_FIELD_SCHEMA` via
  `_coerce_wizard_answer` BEFORE writing into `w`.
- Unknown keys dropped (closed schema).
- Wrong shapes rejected/coerced.
- Server-set keys (`_interview_complete`, `_site_scanned`,
  `_summary`, `_PROTECTED_KEYS`) bypass — they're computed
  server-side, not from client input.
- Vague-answer validation gate (already present) still runs.
"""
from __future__ import annotations
import inspect


def test_complete_endpoint_validates_profile_against_schema():
    """Source-level: api_wizard_complete must reference the
    closed-schema validator for the profile payload."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    has_validator = (
        "_coerce_wizard_answer" in src
        or "_validate_profile_payload" in src
        or "_validate_wizard_profile" in src
        or "_coerce_profile" in src
    )
    assert has_validator, (
        "BRAIN-75 regression: complete must validate the client "
        "profile payload against the BRAIN-73 closed schema. "
        "Pre-fix, _apply_wizard_mutations wrote profile fields "
        "directly to the wizard blob — the last unguarded path "
        "from client input to stored state."
    )


def test_apply_wizard_mutations_filters_unknown_keys():
    """Behavioral: posting a profile with smuggled/unknown keys
    must NOT persist them. Schema is closed."""
    from server import _coerce_wizard_answer, _WIZARD_DROP
    # Known: company_name. Unknown: smuggled.
    assert _coerce_wizard_answer("company_name", "Acme") == "Acme"
    assert _coerce_wizard_answer("_internal_admin_flag", True) is _WIZARD_DROP, (
        "BRAIN-75 regression: smuggled key '_internal_admin_flag' "
        "must be dropped at the boundary."
    )
    assert _coerce_wizard_answer("__proto__", {"polluted": True}) is _WIZARD_DROP, (
        "BRAIN-75 regression: prototype-pollution-style key must "
        "be dropped."
    )


def test_apply_wizard_mutations_rejects_dict_for_scalar_profile_field():
    """Behavioral: a dict shape for a scalar profile field
    must NOT persist as a dict in stored state."""
    from server import _coerce_wizard_answer, _WIZARD_DROP
    # company_name is a scalar string field; dict must be dropped.
    out = _coerce_wizard_answer("company_name", {"evil": "nested"})
    assert out is _WIZARD_DROP, (
        "BRAIN-75 regression: dict for scalar field must drop, "
        "not coerce to str(dict) which leaks the dict repr."
    )


def test_apply_wizard_mutations_filters_nested_lists_in_list_field():
    """Behavioral: list-of-mixed for `regions` must filter to
    strings only."""
    from server import _coerce_wizard_answer
    out = _coerce_wizard_answer(
        "regions",
        [["nested", "list"], "Italy", {"x": 1}, 42, "Spain"]
    )
    assert isinstance(out, list)
    for item in out:
        assert isinstance(item, str)
    assert "Italy" in out and "Spain" in out


def test_apply_wizard_mutations_caps_oversized_string():
    """Behavioral: a 200KB business_description must cap at the
    schema-declared max."""
    from server import _coerce_wizard_answer, _WIZARD_STR_MAX
    huge = "X" * 200_000
    out = _coerce_wizard_answer("business_description", huge)
    assert isinstance(out, str)
    assert len(out) <= _WIZARD_STR_MAX, (
        f"BRAIN-75 regression: business_description capped at "
        f"{len(out)} (max {_WIZARD_STR_MAX})."
    )


def test_complete_validation_runs_before_apply_mutations():
    """Source-level: the schema validation must run BEFORE
    _apply_wizard_mutations (which writes into w). Otherwise
    the unsanitized profile ends up in the off-txn snapshot
    used for brain+dossier compute, even if later writes are
    guarded."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    # Find the apply call site and the validator call site;
    # validator must come first.
    apply_idx = src.find("_apply_wizard_mutations(_w_snap)")
    # Try multiple validator names — accept whichever the
    # implementation chose.
    validator_idx = -1
    for name in (
        "_coerce_wizard_answer",
        "_validate_profile_payload",
        "_validate_wizard_profile",
        "_coerce_profile",
    ):
        idx = src.find(name)
        if idx != -1:
            validator_idx = idx if validator_idx == -1 else min(validator_idx, idx)
    assert apply_idx != -1
    assert validator_idx != -1, "validator not present"
    assert validator_idx < apply_idx, (
        "BRAIN-75 regression: profile schema validation must "
        "happen BEFORE _apply_wizard_mutations runs against "
        "the snapshot, otherwise unvalidated fields propagate "
        "into the brain+dossier compute window."
    )
