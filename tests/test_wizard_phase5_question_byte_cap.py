"""Regression tests for BRAIN-128 (a497): every
phase-5 AI-generated question must be clipped to a
fixed byte budget at UTF-8 boundaries before
persistence AND before API response serialization.
BRAIN-103 capped the LIST count (5 questions max);
this caps the per-ITEM byte size.

Failure mode (Per Huntova engineering review on
LLM output handling + insecure-output guidance):

BRAIN-103 (a472) caps `_phase5_questions` at 5
items. A hallucinating model still produces a
single 50 KB question text, or 50 KB options, or
both — and the cleaner at the phase-5 persist path
just appends them to `cleaned`:

```python
cleaned.append({
    "question": _q_text,        # no byte cap
    "type": _q_type,
    "options": opts,            # no per-option cap
    "placeholder": ...,         # no byte cap
    "prefill": ...,             # no byte cap
})
```

The 5-item count cap then keeps the list at 5, but
each item can be 50 KB → up to 250 KB just for
phase-5 questions in the persisted row. That:
- Bloats the row.
- Weighs down BRAIN-86 canonicalization.
- Slows BRAIN-85 fingerprint cache lookups.
- Poisons clients that try to render the question
  text in a small textarea.
- Floods AI prompts when the question feeds back
  in via `_BRAIN_QUESTIONS` rendering.

Per Huntova engineering review + LLM-validation
guidance: prompt instructions alone do not reliably
control output length. Validated structured output
still needs field-level bounds matching storage and
rendering limits.

Invariants:
- Module-scope constant
  `_WIZARD_PHASE5_QUESTION_BYTES_MAX` (default 4 KiB).
  Phase-5 questions are 1-2 sentences; 4 KiB is
  generous.
- Module-scope constant
  `_WIZARD_PHASE5_OPTION_BYTES_MAX` (default 512 B).
  Options are short labels.
- The phase-5 cleaner clips question text,
  placeholder, prefill, AND each option using
  `_clip_to_byte_budget` before adding to `cleaned`.
- `/api/wizard/status` emit applies defense-in-depth
  clipping on read so legacy rows persisted before
  this cap can't poison clients.
"""
from __future__ import annotations
import inspect


def test_phase5_question_bytes_max_constant_exists():
    """Module-scope cap for phase-5 question text."""
    import server as _s
    val = getattr(_s, "_WIZARD_PHASE5_QUESTION_BYTES_MAX", None)
    assert val is not None, (
        "BRAIN-128 regression: server must expose "
        "`_WIZARD_PHASE5_QUESTION_BYTES_MAX`. "
        "Per-question byte cap complements BRAIN-103's "
        "list-count cap."
    )
    assert isinstance(val, int) and val > 0
    # Sanity bounds: a phase-5 question is a sentence
    # or two. 1 KiB minimum (legitimate prompt + emoji
    # padding); 16 KiB max (anything larger defeats
    # the purpose).
    assert 1024 <= val <= 16384


def test_phase5_option_bytes_max_constant_exists():
    """Module-scope cap for individual select option
    strings."""
    import server as _s
    val = getattr(_s, "_WIZARD_PHASE5_OPTION_BYTES_MAX", None)
    assert val is not None, (
        "BRAIN-128 regression: server must expose "
        "`_WIZARD_PHASE5_OPTION_BYTES_MAX`. Options are "
        "short labels; tighter cap than question text."
    )
    assert isinstance(val, int) and val > 0
    # Options are dropdown labels — typically < 100
    # bytes. 256 minimum, 4 KiB max.
    assert 256 <= val <= 4096


def test_phase5_cleaner_uses_clip_helper():
    """Source-level: the phase-5 cleaner must apply
    `_clip_to_byte_budget` to question text + options
    + placeholder + prefill before appending to
    `cleaned`. Without this, BRAIN-103's count cap
    keeps 5 items but each can be 50 KB."""
    from server import api_wizard_generate_phase5
    src = inspect.getsource(api_wizard_generate_phase5)
    # The cleaner is the inner block that builds
    # `cleaned`. It must reference the byte clipper.
    assert "_clip_to_byte_budget(" in src, (
        "BRAIN-128 regression: api_wizard_generate_phase5 "
        "must call `_clip_to_byte_budget` on phase-5 "
        "question fields before persistence."
    )


def test_phase5_cleaner_clamps_question_text():
    """Source-level: the cleaner specifically clamps
    the question text against
    `_WIZARD_PHASE5_QUESTION_BYTES_MAX`."""
    from server import api_wizard_generate_phase5
    src = inspect.getsource(api_wizard_generate_phase5)
    # Find the `_q_text` extraction + the byte cap
    # constant in proximity.
    assert "_WIZARD_PHASE5_QUESTION_BYTES_MAX" in src, (
        "BRAIN-128 regression: cleaner must reference "
        "`_WIZARD_PHASE5_QUESTION_BYTES_MAX` somewhere "
        "in the question-text clipping path."
    )


def test_phase5_cleaner_clamps_options():
    """Source-level: the cleaner clamps each option
    string."""
    from server import api_wizard_generate_phase5
    src = inspect.getsource(api_wizard_generate_phase5)
    assert "_WIZARD_PHASE5_OPTION_BYTES_MAX" in src, (
        "BRAIN-128 regression: cleaner must reference "
        "`_WIZARD_PHASE5_OPTION_BYTES_MAX` for the "
        "options clipping path."
    )


def test_status_endpoint_clips_phase5_on_emit():
    """Source-level: /api/wizard/status applies
    defense-in-depth clipping on read so a corrupted
    legacy row that escaped the cap on write doesn't
    poison clients."""
    from server import api_wizard_status
    src = inspect.getsource(api_wizard_status)
    # Status must reference the helper or a clipping
    # function on the phase5_questions emit path.
    has_clip = (
        "_clip_to_byte_budget(" in src
        or "_WIZARD_PHASE5_QUESTION_BYTES_MAX" in src
        or "_clip_phase5_questions(" in src
        or "_normalize_phase5_questions(" in src
    )
    assert has_clip, (
        "BRAIN-128 regression: api_wizard_status must "
        "clip `phase5_questions` on emit (read-side "
        "defense-in-depth). Pre-fix it raw-emitted "
        "`w.get('_phase5_questions')` — a legacy row "
        "stored before the cap can poison clients."
    )


def test_clip_helper_round_trip_safe_for_phase5():
    """Behavioral: `_clip_to_byte_budget` is safe for
    phase-5 inputs — multibyte chars don't break the
    output."""
    import server as _s
    cap = _s._WIZARD_PHASE5_QUESTION_BYTES_MAX
    # 4-byte emoji at the boundary should still round-
    # trip.
    text = "What's your goal? 🎯" + "x" * (cap * 2)
    out = _s._clip_to_byte_budget(text, cap)
    # Round-trip valid UTF-8.
    out.encode("utf-8").decode("utf-8")
    assert len(out.encode("utf-8")) <= cap


def test_normalize_phase5_questions_helper_clamps_each_field():
    """If a normalize helper exists, it should clamp
    every text field per question. (Helper-existence
    is acceptable in either inline-clip or extracted-
    helper form; this test runs ONLY when an
    extracted helper exists.)"""
    import server as _s
    fn = getattr(_s, "_normalize_phase5_questions", None)
    if fn is None:
        # Acceptable: clipping happens inline in the
        # cleaner. Other tests cover that path.
        return
    cap_q = _s._WIZARD_PHASE5_QUESTION_BYTES_MAX
    cap_o = _s._WIZARD_PHASE5_OPTION_BYTES_MAX
    out = fn([
        {
            "question": "Q" * 100_000,
            "type": "single_select",
            "options": ["O" * 50_000, "O" * 50_000],
            "placeholder": "P" * 100_000,
            "prefill": "",
        },
    ])
    assert isinstance(out, list)
    assert len(out) >= 0
    if out:
        item = out[0]
        assert len(item["question"].encode("utf-8")) <= cap_q
        assert len(item["placeholder"].encode("utf-8")) <= cap_q
        for opt in item.get("options", []):
            assert len(opt.encode("utf-8")) <= cap_o
