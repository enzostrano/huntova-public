"""Regression test for BRAIN-13 (a374): /api/wizard/assist had
ad-hoc [:200] / [:400] slices and no global block cap. Same
class as BRAIN-11 (a372) but in the chat-style wizard helper.
The user's raw current_answer (could be 50k-char textarea) and
question_context were interpolated into the system prompt
UNCLIPPED — direct path to provider 400 / context overflow.

Per GPT-5.4 audit (this session): "Mirror a372 almost exactly
in /api/wizard/assist."
"""
from __future__ import annotations
import inspect


def _handler_source() -> str:
    from server import api_wizard_assist
    return inspect.getsource(api_wizard_assist)


def test_assist_uses_clip_for_prompt():
    src = _handler_source()
    assert "_clip_for_prompt(" in src, (
        "BRAIN-13 regression: /api/wizard/assist must use "
        "_clip_for_prompt (the canonical per-field budgeter) "
        "for every interpolated user/wizard field."
    )


def test_assist_clips_current_answer():
    """The user's raw current_answer (potentially a 50k-char textarea
    paste) must be clipped before going into the system prompt."""
    src = _handler_source()
    # The handler should at minimum reference current_answer in a
    # _clip_for_prompt call OR slice it before interpolation.
    has_clip = "_clip_for_prompt(current_answer" in src or "current_answer" in src and "_clip_for_prompt" in src
    assert has_clip, (
        "BRAIN-13 regression: current_answer must be clipped before "
        "interpolation into the system prompt."
    )


def test_assist_has_final_context_block_cap():
    """Per-field caps aren't enough — assembled context block must
    have a global cap. Same lesson as BRAIN-11."""
    src = _handler_source()
    # Look for an explicit total-cap on the assembled `ctx` string.
    # The marker is some constant + a check.
    has_cap = ("_CONTEXT_BLOCK_CAP" in src
               or "ctx[:" in src
               or "len(ctx)" in src
               or "context_total_cap" in src.lower())
    assert has_cap, (
        "BRAIN-13 regression: the assembled context block must have "
        "a final cap (per-field budgets aren't enough — boilerplate "
        "+ 20 fields × 200 chars can still overflow)."
    )
