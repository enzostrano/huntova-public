"""Regression tests for BRAIN-98 (a467): /api/wizard/save-progress
must enforce an overall answers-dict size cap, not just per-field
coercion.

Failure mode (Per Huntova engineering review on aggregate
payload bounding):

BRAIN-73's `_coerce_wizard_answer` enforces per-field shape +
length caps (50KB string, 200 list items, schema-restricted
keys). But the answers dict itself has no aggregate bound —
a client can send a payload like:

    {"company_name": "Acme",
     "p5_1": "...", "p5_2": "...", ..., "p5_5000": "..."}

Each individual key is in the schema (phase-5 dynamic
prefixed `p5_*` keys are accepted) and each value is small.
Per-field validation passes. The merged dict bloats
`user_settings.data` by megabytes:

- Brain build, dossier, prompt assembly all iterate over the
  blob — slow.
- `db.merge_settings` reads + writes the full row every save
  — measurable WAL pressure on SQLite.
- JSON parse on every `get_settings` call gets slower over
  time.
- `_canonicalize_complete_payload` (BRAIN-86) walks the dict
  to compute the fingerprint — expensive on bloated state.

Standard API-validation guidance: bound aggregate posted data,
not just per-field. Per-field caps stop one giant string;
aggregate caps stop death-by-thousand-cuts.

Invariants:
- A new constant `_WIZARD_ANSWERS_MAX_KEYS` (~150) bounds the
  total number of keys the merged answers dict can hold.
- `_merge_wizard_answers` (called by save-progress) drops keys
  past the cap rather than accept them.
- The cap is generous — wizard has at most ~9 base + ~5
  phase-5 + ~30 legacy mapping keys = ~44, so 150 is 3-4×
  headroom for future-proofing without enabling the abuse.
- Existing keys in `prev` are preserved when the cap is hit;
  only NEW incoming keys past the cap are dropped (so a
  legitimate save that touches existing fields can't be
  blocked by an attacker who pre-bloated the row).
- Logging surfaces when the cap is hit so an operator can
  spot the abuse pattern.
"""
from __future__ import annotations
import inspect


def test_aggregate_keys_cap_constant_exists():
    """Source-level: the cap must be a named module-level
    constant so an operator can tune it without grepping the
    helper body."""
    import server as _s
    cap = getattr(_s, "_WIZARD_ANSWERS_MAX_KEYS", None)
    assert cap is not None, (
        "BRAIN-98 regression: `_WIZARD_ANSWERS_MAX_KEYS` "
        "constant must be exposed on server."
    )
    # 100-300 is the reasonable range. Below 50 cuts into
    # legitimate phase-5 expansion; above 500 stops bounding
    # in any meaningful way.
    assert isinstance(cap, int) and 100 <= cap <= 300, (
        f"BRAIN-98 regression: cap {cap} unreasonable — "
        f"expected 100-300 (~3-4× the natural ~44-key wizard "
        f"surface area)."
    )


def test_merge_helper_references_aggregate_cap():
    """Source-level: `_merge_wizard_answers` must reference
    `_WIZARD_ANSWERS_MAX_KEYS` so the cap is enforced at the
    centralized merge boundary (parity with
    `_coerce_wizard_answer` per-field validation)."""
    from server import _merge_wizard_answers
    src = inspect.getsource(_merge_wizard_answers)
    assert "_WIZARD_ANSWERS_MAX_KEYS" in src, (
        "BRAIN-98 regression: `_merge_wizard_answers` must "
        "enforce the aggregate cap. Per-field validation "
        "alone allows abusive many-small-fields payloads."
    )


def test_merge_drops_excess_keys_when_cap_exceeded():
    """Behavioral: when the merged dict would exceed the cap,
    new incoming keys past the cap must drop, but existing
    `prev` keys must stay (don't punish a legitimate user
    whose row is already at the boundary from prior saves)."""
    from server import _merge_wizard_answers, _WIZARD_ANSWERS_MAX_KEYS
    # Seed prev with cap keys (using p5_* prefix which is in
    # the schema's str_or_list bucket).
    prev = {f"p5_{i}": f"value-{i}" for i in range(_WIZARD_ANSWERS_MAX_KEYS)}
    # Send incoming with one new key.
    incoming = {"p5_overflow": "should drop"}
    out = _merge_wizard_answers(prev, incoming)
    assert len(out) <= _WIZARD_ANSWERS_MAX_KEYS, (
        f"BRAIN-98 regression: merged dict has {len(out)} "
        f"keys, cap is {_WIZARD_ANSWERS_MAX_KEYS}. New key "
        f"should have dropped instead of pushing past cap."
    )
    assert "p5_overflow" not in out, (
        "BRAIN-98 regression: overflow key must drop, not "
        "land in the persisted state."
    )
    # All prev keys must survive.
    for i in range(_WIZARD_ANSWERS_MAX_KEYS):
        assert f"p5_{i}" in out


def test_merge_accepts_payload_under_cap():
    """Behavioral: a small payload under the cap merges
    normally."""
    from server import _merge_wizard_answers
    prev = {"company_name": "Acme"}
    incoming = {"target_clients": "DTC skincare brands"}
    out = _merge_wizard_answers(prev, incoming)
    assert out.get("company_name") == "Acme"
    assert out.get("target_clients") == "DTC skincare brands"


def test_merge_aggregate_cap_doesnt_break_brain6_noop():
    """Don't regress BRAIN-6: empty incoming must still be a
    no-op. The aggregate cap mustn't accidentally break the
    empty-payload short-circuit."""
    from server import _merge_wizard_answers
    prev = {f"p5_{i}": f"v-{i}" for i in range(40)}
    out = _merge_wizard_answers(prev, {})
    assert out == prev, (
        "BRAIN-98 regression: BRAIN-6 empty-payload no-op "
        "broken by aggregate-cap addition."
    )


def test_merge_overflow_logs_observability_marker():
    """Source-level: when the cap fires, the merge helper
    should print or log so an operator can spot the abuse."""
    from server import _merge_wizard_answers
    src = inspect.getsource(_merge_wizard_answers)
    has_log = (
        "print(" in src or "log" in src.lower()
        or "_overflow" in src
    )
    assert has_log, (
        "BRAIN-98 regression: cap-trip path should log so "
        "operators can spot abuse before it becomes a "
        "support ticket."
    )
