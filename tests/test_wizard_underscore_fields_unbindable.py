"""Regression tests for BRAIN-99 (a468): client-supplied
save-progress payloads must never bind underscore-prefixed
server-owned fields, regardless of what the BRAIN-73 schema
declares.

Failure mode (Per Huntova engineering review on
mass-assignment / OWASP Object Property Manipulation):

The BRAIN-73 `_WIZARD_FIELD_SCHEMA` has entries for several
underscore-prefixed control fields:

- `_wizard_phase`: "int"   — monotonic max phase (BRAIN-3)
- `_wizard_revision`: "int" — optimistic-concurrency token (BRAIN-14)
- `_wizard_epoch`: "int"   — reset generation token (BRAIN-81)
- `_wizard_cursor`: "int"  — current view position (BRAIN-87)
- `_wizard_confidence`: "int"
- `_train_count`: "int"
- `_last_trained`: "str"
- `_dna_state`: omitted from schema today, but dna_state is
  exposed by status, set by complete + _gen_dna durably
- `_last_complete_fingerprint`: also a server-owned field,
  not in schema but lives at top of wizard

These entries exist because the SERVER'S OWN mutator code
writes them (`w["_wizard_phase"] = _monotonic_phase(...)`,
`w["_wizard_revision"] = ...`, etc). But
`_merge_wizard_answers` calls `_coerce_wizard_answer` for
EVERY incoming key from a client save-progress payload —
so a client could send:

```json
{"answers": {
  "company_name": "Acme",
  "_wizard_phase": 999,             // skip past validation
  "_wizard_revision": 1,            // collide with real saves
  "_wizard_epoch": 0,               // un-bump reset
  "_train_count": 0,                // erase audit
  "_last_complete_fingerprint": "0" // poison BRAIN-85 cache
}}
```

The `int` schema entries pass coercion. The fields land in
`_wizard_answers` and trigger downstream confusion: monotonic
phase clamps protect some, but cache fingerprint + dna_state
+ epoch are unguarded.

OWASP guidance is blunt: allowlist client-bindable fields,
don't blocklist protected ones. Blocklists drift; allowlists
fail-closed on new additions.

Invariants:
- `_coerce_wizard_answer` REJECTS any key starting with `_`
  regardless of schema entry. Underscore-prefix is the
  server-owned namespace.
- Phase-5 dynamic keys (`p5_*`) still work — they're
  letter-prefixed.
- A behavioral test confirms: send all five sensitive
  underscore fields plus a normal answer; only the normal
  answer lands; every underscore field is unchanged in the
  merged blob.
- The reject-underscore guard logs (or is otherwise
  observable) so an operator can spot the abuse pattern.
"""
from __future__ import annotations
import inspect


def test_coerce_rejects_underscore_prefix_keys():
    """Source-level: `_coerce_wizard_answer` must reject any
    key starting with `_`. Allowlist semantics, not blocklist."""
    from server import _coerce_wizard_answer
    src = inspect.getsource(_coerce_wizard_answer)
    # Look for a guard checking for underscore prefix.
    has_guard = (
        'startswith("_")' in src
        or "startswith('_')" in src
        or "key.startswith(\"_\")" in src
        or 'key[0] == "_"' in src
        or "key[0] == '_'" in src
    )
    assert has_guard, (
        "BRAIN-99 regression: `_coerce_wizard_answer` must "
        "explicitly reject keys starting with `_`. "
        "Underscore-prefix is the server-owned namespace; "
        "client save-progress writes must not bind those."
    )


def test_underscore_keys_drop_via_merge():
    """Behavioral: send a payload with five sensitive
    underscore-prefixed fields plus one normal answer. The
    normal answer must persist; every underscore field must
    stay at its prev value."""
    from server import _merge_wizard_answers
    prev = {
        "company_name": "Acme",
        "_wizard_phase": 5,
        "_wizard_revision": 12,
        "_wizard_epoch": 2,
        "_wizard_cursor": 3,
        "_train_count": 4,
        "_last_complete_fingerprint": "good-hash-aaaa",
        "_dna_state": "ready",
    }
    incoming = {
        "company_name": "Acme V2",
        "_wizard_phase": 999,
        "_wizard_revision": 0,
        "_wizard_epoch": 0,
        "_wizard_cursor": -1,
        "_train_count": 0,
        "_last_complete_fingerprint": "attacker-controlled",
        "_dna_state": "failed",
    }
    out = _merge_wizard_answers(prev, incoming)

    # Normal answer updated.
    assert out["company_name"] == "Acme V2", (
        "BRAIN-99 regression: legitimate answer was "
        "blocked along with underscore fields."
    )

    # Every underscore field unchanged.
    for k, expected in (
        ("_wizard_phase", 5),
        ("_wizard_revision", 12),
        ("_wizard_epoch", 2),
        ("_wizard_cursor", 3),
        ("_train_count", 4),
        ("_last_complete_fingerprint", "good-hash-aaaa"),
        ("_dna_state", "ready"),
    ):
        assert out.get(k) == expected, (
            f"BRAIN-99 regression: client overwrote "
            f"server-owned field `{k}`. Expected {expected!r}, "
            f"got {out.get(k)!r}. Mass-assignment vulnerability "
            f"is open."
        )


def test_phase5_keys_still_work():
    """Don't regress: dynamic phase-5 prefixed keys (`p5_*`)
    are letter-prefixed, NOT underscore — they must continue
    to merge through the normal coerce path."""
    from server import _merge_wizard_answers
    prev = {"company_name": "Acme"}
    incoming = {"p5_1": "phase-5 answer 1", "p5_2": "phase-5 answer 2"}
    out = _merge_wizard_answers(prev, incoming)
    assert out["p5_1"] == "phase-5 answer 1"
    assert out["p5_2"] == "phase-5 answer 2"


def test_legitimate_keys_unaffected_by_underscore_block():
    """Don't regress: every non-underscore wizard schema key
    continues to merge normally."""
    from server import _merge_wizard_answers
    prev = {}
    incoming = {
        "company_name": "Acme",
        "company_website": "https://acme.com",
        "business_description": "We help DTC skincare brands scale.",
        "target_clients": "Series A B2B SaaS founders",
        "regions": ["United States", "Italy"],
        "services": ["Shopify migration", "CRO retainer"],
        "buyer_roles": ["Founder", "Marketing Director"],
    }
    out = _merge_wizard_answers(prev, incoming)
    for k in incoming:
        assert k in out, (
            f"BRAIN-99 regression: legitimate field `{k}` was "
            f"dropped by the underscore-block."
        )


def test_drop_helper_logs_or_is_observable():
    """Source-level: the underscore-block path should be
    observable — either a log line, a counter, or a comment
    that lets ops spot the abuse pattern in code review."""
    from server import _coerce_wizard_answer
    src = inspect.getsource(_coerce_wizard_answer)
    # A comment referencing the security rationale is enough
    # for now — when the helper is called millions of times
    # logging every drop would be noise.
    has_marker = (
        "BRAIN-99" in src
        or "mass-assignment" in src.lower()
        or "underscore" in src.lower()
        or "server-owned" in src.lower()
        or "OWASP" in src
    )
    assert has_marker, (
        "BRAIN-99 regression: the underscore-block path "
        "needs a comment naming the security rationale so "
        "future code reviewers don't accidentally re-allow "
        "underscore keys for some 'cleanup' refactor."
    )
