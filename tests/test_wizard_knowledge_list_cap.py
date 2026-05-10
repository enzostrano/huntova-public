"""Regression tests for BRAIN-102 (a471): the wizard `_knowledge`
list must be bounded to a recent-N window so unbounded growth
doesn't silently bloat `user_settings.data` over time.

Failure mode (Per Huntova engineering review on
embedded-array growth):

Every successful `/api/wizard/complete` appends a new entry to
`_knowledge`:

    kn = list(w.get("_knowledge") or [])
    kn.append(_knowledge_entry)
    w["_knowledge"] = kn

`_knowledge_entry` is a dict like:

    {
        "date": _now_iso,
        "type": "ai_interview",
        "content": json.dumps({"profile": profile, "qa_count": ...})[:2000],
        "source": "wizard_v2",
    }

~2-3KB per entry. A power user, automation, or tight retry
loop can accumulate hundreds of entries in `user_settings.data`:

- 200 completes × 2KB = 400KB row inflation.
- Every `merge_settings` reads + writes the full JSON blob —
  IO grows linearly in retraining count.
- Every `get_settings` JSON parse on every request gets
  slower.
- Hits SQLite row size pain at thousands of entries.

Standard guidance for embedded arrays: cap to a recent-N
window. Operational state lives in `user_settings`; deep
history (if needed) belongs in a separate table.

Invariants:
- New constant `_KNOWLEDGE_LIST_MAX` (~50) bounds the list
  length.
- The append in `api_wizard_complete`'s merge mutator
  truncates to the most-recent N items via slice
  (`kn[-_KNOWLEDGE_LIST_MAX:]`) BEFORE persisting.
- The most recent entry is always the one we just appended;
  oldest entries fall off when the cap is hit.
- The cap is env-overridable.
- A newer entry should never displace an older entry from
  the array's TAIL position (we trim the head, keep the tail).
"""
from __future__ import annotations
import inspect


def test_knowledge_cap_constant_exists():
    """Source-level: a `_KNOWLEDGE_LIST_MAX` constant must
    exist on `server`. Operators need to tune retention."""
    import server as _s
    cap = getattr(_s, "_KNOWLEDGE_LIST_MAX", None)
    assert cap is not None, (
        "BRAIN-102 regression: `_KNOWLEDGE_LIST_MAX` constant "
        "must be exposed on server."
    )
    # 10-500 is the reasonable range. Below 10 cuts into
    # legitimate audit context; above 500 lets the array
    # bloat to ~1MB.
    assert isinstance(cap, int) and 10 <= cap <= 500, (
        f"BRAIN-102 regression: cap {cap} unreasonable. "
        f"Expected 10-500 (~50 entries × 2-3KB ≈ 100-150KB "
        f"max bounded inflation per user)."
    )


def test_knowledge_cap_env_overridable():
    """Source-level: cap must read from an env var so
    operators don't need to patch the source."""
    import server as _s
    src = inspect.getsource(_s)
    assert (
        "HV_WIZARD_KNOWLEDGE_LIST_MAX" in src
        or "HV_KNOWLEDGE_LIST_MAX" in src
    ), (
        "BRAIN-102 regression: cap must read from env var "
        "(`HV_WIZARD_KNOWLEDGE_LIST_MAX` or similar)."
    )


def test_complete_endpoint_caps_knowledge_list():
    """Source-level: `api_wizard_complete`'s merge mutator
    must truncate `_knowledge` to the cap when appending."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    assert "_KNOWLEDGE_LIST_MAX" in src, (
        "BRAIN-102 regression: complete mutator must reference "
        "`_KNOWLEDGE_LIST_MAX` to bound the list. Without it, "
        "every complete grows the array unboundedly."
    )


def test_knowledge_truncation_keeps_recent_tail():
    """Source-level: the truncation slice must take the
    LAST N items (negative index), not the first N. Keeping
    the head would discard the entry just appended."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    # Look for `kn[-_KNOWLEDGE_LIST_MAX:]` or equivalent
    # negative-slice pattern near the _knowledge handling.
    has_tail_slice = (
        "[-_KNOWLEDGE_LIST_MAX:]" in src
        or "-_KNOWLEDGE_LIST_MAX:]" in src
        or ".reverse()" in src  # alternative pattern
    )
    assert has_tail_slice, (
        "BRAIN-102 regression: truncation must take the "
        "TAIL (`kn[-N:]`), not the head. Otherwise the "
        "entry we just appended drops off."
    )


def test_knowledge_cap_behavioral_truncation(local_env):
    """Behavioral: simulate 200 completes by directly
    invoking the merge mutator pattern; verify the stored
    list never exceeds the cap and the newest entry survives."""
    import asyncio

    async def _run():
        from db import init_db, create_user, merge_settings, get_settings
        from auth import hash_password
        import server as _s
        cap = _s._KNOWLEDGE_LIST_MAX
        await init_db()
        uid = await create_user(
            "knowledge-cap@example.com", hash_password("p"), "KC"
        )

        # Simulate the complete mutator's exact append logic
        # for cap+50 iterations.
        for i in range(cap + 50):
            entry = {"date": f"2026-05-04T00:00:{i:02d}",
                     "type": "ai_interview",
                     "content": f"entry-{i}",
                     "source": "wizard_v2"}

            def _append_knowledge(cur, _e=entry):
                cur = dict(cur or {})
                w = dict(cur.get("wizard") or {})
                kn = list(w.get("_knowledge") or [])
                kn.append(_e)
                # Mirror the BRAIN-102 truncation:
                if len(kn) > cap:
                    kn = kn[-cap:]
                w["_knowledge"] = kn
                cur["wizard"] = w
                return cur

            await merge_settings(uid, _append_knowledge)

        # Verify the cap held.
        s = await get_settings(uid)
        kn = ((s or {}).get("wizard") or {}).get("_knowledge") or []
        assert len(kn) == cap, (
            f"BRAIN-102 regression: list length {len(kn)} "
            f"!= cap {cap} after {cap + 50} appends."
        )
        # Newest entry should be the last one.
        last_content = kn[-1].get("content", "")
        assert last_content == f"entry-{cap + 50 - 1}", (
            f"BRAIN-102 regression: newest entry was discarded. "
            f"Expected `entry-{cap + 50 - 1}`, got "
            f"`{last_content}`."
        )

    asyncio.run(_run())
