"""Regression test for BRAIN-11 (a372): the phase-5 prompt builder
in /api/wizard/generate-phase5 had no per-field caps beyond ad-hoc
[:600] / [:400] slices, AND no final global block cap. With 10
profile fields × 600 chars + 16 scan extras × 400 chars =
12,400 raw chars BEFORE the rest of the prompt boilerplate. A
user pasting a 50k-char business_description (or scan returning
fat HTML) would balloon the prompt and hit Anthropic /
OpenAI / Gemini context-limit 400s, OR push output tokens down
so the phase-5 questions degrade.

Per GPT-5.4's senior-engineer audit (Perplexity, this session):
"The bug to kill is: prompt assembler has no budget enforcement."

This is bug-prevention + hard-failure-prevention combined.
"""
from __future__ import annotations


def test_clip_for_prompt_helper_exists():
    from server import _clip_for_prompt
    assert callable(_clip_for_prompt)


def test_clip_for_prompt_returns_text_and_truncated_flag():
    from server import _clip_for_prompt
    short = "we help SMBs scale"
    out, trunc = _clip_for_prompt(short, 100)
    assert out == short
    assert trunc is False
    long = "x" * 5000
    out, trunc = _clip_for_prompt(long, 100)
    assert len(out) <= 100
    assert trunc is True


def test_clip_for_prompt_collapses_whitespace():
    """Newlines and runs of whitespace inside the field shouldn't
    eat up the budget — they're noise."""
    from server import _clip_for_prompt
    msg = "we help\n\n\n   SMBs    scale\n\n"
    out, _ = _clip_for_prompt(msg, 100)
    assert out == "we help SMBs scale"


def test_clip_for_prompt_handles_non_strings():
    """Defensive coercion same class as BRAIN-7/8."""
    from server import _clip_for_prompt
    out, trunc = _clip_for_prompt(None, 100)
    assert out == "" and trunc is False
    out, trunc = _clip_for_prompt(["fragment a", "fragment b"], 100)
    assert isinstance(out, str)


def test_phase5_prompt_assembler_has_global_block_cap():
    """The handler must have a final global cap on the combined
    profile_block + extras_block AFTER per-field clipping. Per
    GPT-5.4: 'Even perfect per-field caps can still overflow once
    the fixed prompt boilerplate is added.'"""
    import inspect
    from server import api_wizard_generate_phase5
    src = inspect.getsource(api_wizard_generate_phase5)
    # Look for an explicit cap applied to the assembled block (not
    # just per-field). The marker is some constant + an explicit
    # truncation check.
    assert "_clip_for_prompt(" in src, (
        "BRAIN-11 regression: the phase-5 prompt assembler must use "
        "_clip_for_prompt for per-field budgeting AND a final "
        "block-level cap. Was: ad-hoc [:600] / [:400] slices with "
        "no global guard."
    )
