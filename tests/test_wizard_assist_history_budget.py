"""Regression tests for BRAIN-71 (a432): /api/wizard/assist must
operate within a fixed history budget regardless of how many
prior turns or how large any single paste was.

Failure mode (per GPT-5.4 chat-history-bloat audit):

Pre-fix, `api_wizard_assist` did:
    for turn in (chat_history or [])[-10:]:
        if turn.get("role") == "user" and turn.get("text"):
            messages.append({"role": "user", "content": turn["text"]})
        elif turn.get("role") == "bot" and turn.get("text"):
            messages.append({"role": "assistant", "content": turn["text"]})

Two failure axes:

1. Per-turn unbounded — any single turn's `text` was appended raw.
   So one 50KB paste in turn N-1 gets re-sent on every subsequent
   assist call for the rest of the wizard session. The user's BYOK
   provider gets billed for the same 50KB on every assist turn.

2. Total-history unbounded — 10 turns × unbounded per-turn =
   unbounded total. Even with `_CONTEXT_BLOCK_CAP=8000` on the
   wizard-fields ctx block, the chat-history gets stacked on top
   with no global cap. Eventually hits provider context limits;
   401/400/timeout failures bubble up to the user as cryptic AI
   errors.

3. The 10-turn count cap is a safety floor but doesn't bound bytes.

Invariants:
- Per-turn cap (~600 chars) via `_clip_for_prompt` — same pattern
  as the ctx fields.
- Total history budget (~6000-8000 chars) enforced AFTER per-turn
  clipping, walking newest→oldest so the newest turn is always
  preserved.
- Older turns dropped (not summarized — defensive simplicity)
  once the total budget is exhausted.
- Prompt budget logging (`truncated_fields`) records when history
  was trimmed, same pattern as other budget enforcement.
"""
from __future__ import annotations
import inspect


def test_assist_clips_each_turn_to_per_turn_budget():
    """Source-level: each chat-history turn must pass through
    `_clip_for_prompt` with a per-turn budget. Otherwise one
    50KB paste poisons every future assist call."""
    from server import api_wizard_assist
    src = inspect.getsource(api_wizard_assist)
    # The fix must invoke _clip_for_prompt INSIDE the history
    # loop, not just on the wizard-fields context block.
    # Anchor on the chat_history reference and verify _clip_for_prompt
    # is called per-turn with a budget constant.
    assert "chat_history" in src
    has_per_turn_clip = (
        "_ASSIST_HISTORY_TURN_BUDGET" in src
        or "_HISTORY_TURN_BUDGET" in src
        or "_TURN_BUDGET" in src
    )
    assert has_per_turn_clip, (
        "BRAIN-71 regression: assist must define a per-turn "
        "history budget constant and apply _clip_for_prompt to "
        "each turn's text. Without this, any single 50KB paste "
        "in a prior turn gets re-sent on every assist call until "
        "the wizard is closed."
    )


def test_assist_enforces_total_history_budget():
    """Source-level: the assembled history must be capped to a
    fixed total budget regardless of turn count. Even with
    per-turn clips, 10 turns × 600 chars × N retries can stack."""
    from server import api_wizard_assist
    src = inspect.getsource(api_wizard_assist)
    has_total_cap = (
        "_ASSIST_HISTORY_TOTAL" in src
        or "_HISTORY_TOTAL_CAP" in src
        or "_HISTORY_BUDGET" in src
    )
    assert has_total_cap, (
        "BRAIN-71 regression: assist must enforce a total history "
        "budget constant separate from per-turn clipping, walked "
        "newest→oldest so older turns drop first."
    )


def test_assist_preserves_newest_turn_when_budget_exhausted():
    """Source-level: the budget walk must be oldest-drop, not
    newest-drop. The current message + most recent turn carry the
    user's actual intent; older context is helpful but optional."""
    from server import api_wizard_assist
    src = inspect.getsource(api_wizard_assist)
    # The walk should iterate the history in REVERSE so newer
    # turns get budget-prioritized. Accept either a `reversed(...)`
    # call or explicit slice operations indicating newest-first.
    has_reversed = (
        "reversed(" in src
        or "[::-1]" in src
        or ".reverse()" in src
    )
    assert has_reversed, (
        "BRAIN-71 regression: history budget must be applied "
        "walking newest→oldest. Otherwise an oversized 50KB paste "
        "in turn 1 eats the entire budget and the user's actual "
        "current question (in turn N) gets dropped."
    )


def test_assist_still_caps_turn_count():
    """Don't regress: the existing `[-10:]` count cap is still a
    cheap safety floor — keep it. Combined with per-turn AND
    total budgets, three layers of defense."""
    from server import api_wizard_assist
    src = inspect.getsource(api_wizard_assist)
    # Look for some form of count cap. Either the original [-10:]
    # or a renamed constant.
    has_count_cap = (
        "[-10:]" in src
        or "[-8:]" in src
        or "[-12:]" in src
        or "_HISTORY_TURN_COUNT" in src
        or "_MAX_HISTORY_TURNS" in src
        or "_ASSIST_HISTORY_MAX_TURNS" in src
    )
    assert has_count_cap, (
        "BRAIN-71 regression: don't drop the count cap on history "
        "turns while adding budget enforcement. Both layers are "
        "useful — count-cap fails fast on absurd inputs, byte-cap "
        "fails fast on absurd payloads."
    )


def test_assist_logs_history_truncation_for_observability():
    """Source-level: when history is trimmed, the existing
    `truncated_fields` log must record it — same pattern as the
    BRAIN-13 ctx-block truncation. Operators need to know when
    user-visible state is being silently dropped."""
    from server import api_wizard_assist
    src = inspect.getsource(api_wizard_assist)
    # Must reference a history-related truncation marker.
    has_history_marker = (
        "__history_block__" in src
        or "__history__" in src
        or "_history_clip" in src
        or "history_turns_dropped" in src
    )
    assert has_history_marker, (
        "BRAIN-71 regression: history budget enforcement must "
        "log to truncated_fields when it kicks in. Otherwise we "
        "have no signal when user state is being dropped from "
        "their assist context."
    )
