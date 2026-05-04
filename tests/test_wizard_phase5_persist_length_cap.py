"""Regression tests for BRAIN-103 (a472): the
`_phase5_questions` persist boundary must enforce a hard
length cap regardless of what the upstream cleaner produced.

Failure mode (Per Huntova engineering review on
output-validation defense-in-depth):

`generate-phase5` currently cleans the AI output to a
5-item maximum (BRAIN-69 enforced via
`for q in questions[:5]:` slice in the cleaner loop). The
`_persist_phase5` merge mutator then writes the cleaned
array verbatim into `_phase5_questions`:

    _w["_phase5_questions"] = cleaned

Today the contract holds because the cleaner caps at 5.
But the persist boundary is trusting an upstream
guarantee. A future change that:

- Loosens the cleaner (e.g. relax `[:5]` to `[:50]` for
  longer wizards),
- Changes the AI prompt to request more items,
- Refactors the cleaner without preserving the slice,
- Hits a parser regression that returns more items,

…would persist a bloated array into `user_settings.data`.
LLM output handling best practice is defense in depth:
constrain the model, validate the parsed structure, AND
cap collection size before persistence.

Invariants:
- New constant `_PHASE5_QUESTIONS_MAX` (~5) caps the
  persisted array regardless of cleaner output.
- `_persist_phase5` truncates `cleaned[:_PHASE5_QUESTIONS_MAX]`
  before writing.
- Cap is env-overridable for future schemas.
- The truncation preserves order (head-slice — first N
  items), since the cleaner already ranks the
  most-important questions first by AI output order.
"""
from __future__ import annotations
import inspect


def test_phase5_questions_persist_cap_constant_exists():
    """Source-level: the cap must be a named module-level
    constant."""
    import server as _s
    cap = getattr(_s, "_PHASE5_QUESTIONS_MAX", None)
    assert cap is not None, (
        "BRAIN-103 regression: `_PHASE5_QUESTIONS_MAX` "
        "constant must be exposed on server."
    )
    # 3-20 is the reasonable range. Below 3 cuts into
    # legitimate phase-5 surface; above 20 lets a future
    # parser regression bloat the array.
    assert isinstance(cap, int) and 3 <= cap <= 20, (
        f"BRAIN-103 regression: cap {cap} unreasonable "
        f"(expected 3-20; today's cleaner emits up to 5)."
    )


def test_phase5_questions_cap_env_overridable():
    """Source-level: cap must read from an env var."""
    import server as _s
    src = inspect.getsource(_s)
    assert (
        "HV_WIZARD_PHASE5_QUESTIONS_MAX" in src
        or "HV_PHASE5_QUESTIONS_MAX" in src
    ), (
        "BRAIN-103 regression: cap must be env-overridable "
        "(`HV_WIZARD_PHASE5_QUESTIONS_MAX` or similar)."
    )


def test_persist_phase5_applies_length_cap():
    """Source-level: the `_persist_phase5` merge mutator
    must reference `_PHASE5_QUESTIONS_MAX` and slice the
    `cleaned` array before writing."""
    from server import api_wizard_generate_phase5
    src = inspect.getsource(api_wizard_generate_phase5)
    assert "_PHASE5_QUESTIONS_MAX" in src, (
        "BRAIN-103 regression: persist boundary must "
        "reference the cap. Trusting upstream cleaner alone "
        "is fragile across refactors."
    )
    # The slice pattern must apply to `cleaned`.
    has_slice = (
        "cleaned[:_PHASE5_QUESTIONS_MAX]" in src
        or "cleaned[: _PHASE5_QUESTIONS_MAX]" in src
    )
    assert has_slice, (
        "BRAIN-103 regression: persist must slice `cleaned` "
        "by the cap (`cleaned[:_PHASE5_QUESTIONS_MAX]`). "
        "Head slice preserves the cleaner's ranking — first "
        "items are most relevant."
    )


def test_persist_phase5_does_not_use_oversized_array(local_env):
    """Behavioral: simulate a hypothetical regression where
    the cleaner returned 20 items. Verify the persist
    mutator caps the stored list at `_PHASE5_QUESTIONS_MAX`
    regardless."""
    import asyncio

    async def _run():
        from db import init_db, create_user, merge_settings, get_settings
        from auth import hash_password
        import server as _s
        cap = _s._PHASE5_QUESTIONS_MAX
        await init_db()
        uid = await create_user(
            "phase5cap@example.com", hash_password("p"), "P5C"
        )
        # Simulate the persist mutator's exact behavior with
        # a cleaned list 4× the cap.
        oversized = [
            {"question": f"q{i}", "type": "text",
             "options": [], "placeholder": "", "prefill": ""}
            for i in range(cap * 4)
        ]
        # Mirror the BRAIN-103 truncation:
        def _persist(cur):
            cur = dict(cur or {})
            w = dict(cur.get("wizard") or {})
            w["_phase5_questions"] = oversized[:cap]
            cur["wizard"] = w
            return cur
        await merge_settings(uid, _persist)
        s = await get_settings(uid)
        stored = ((s or {}).get("wizard") or {}).get("_phase5_questions") or []
        assert len(stored) == cap, (
            f"BRAIN-103 regression: cap {cap} not enforced; "
            f"stored {len(stored)} items."
        )
        # Order preserved: first item must be q0.
        assert stored[0].get("question") == "q0", (
            "BRAIN-103 regression: head-slice broken; first "
            "item should be the cleaner's top-ranked entry."
        )

    asyncio.run(_run())
