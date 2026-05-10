"""Regression tests for BRAIN-69 (a430): /api/wizard/generate-phase5
hardening — server must fail closed on malformed AI output, client
must reject stale generation responses.

Failure mode (per GPT-5.4 audit on async-race + LLM-output-fragility):

CLIENT race:
- User clicks "Re-train" mid-generation. The first phase-5 fetch is
  still in flight when the wizard re-renders with phase5Tried=false.
  A second generation fires. If the older fetch resolves AFTER the
  newer one, its (now-stale) questions get appended on top of the
  newer ones. Result: _BRAIN_QUESTIONS contains a mix of stale +
  fresh phase-5 questions, double-counted (10 instead of 5).

SERVER fragility:
- AI returns a list with junk items: `{"question": "", "type": "x"}`
  or `{"type": "single_select", "options": []}` or `{"type":
  "weird_type"}`. Pre-fix, the cleaner returned them with empty
  strings / empty options / unknown type — the wizard then renders
  a useless empty question, or worse a select with no options
  (user clicks Continue and there's nothing to select).
- AI returns malformed JSON. _extract_json may parse half the
  array; cleaner returns whatever's there. User sees a half-broken
  follow-up flow with no signal that something failed.

Invariants:
1. Items with empty/whitespace-only question text are dropped.
2. Items with type='single_select' or 'multi_select' but missing
   or empty `options` are dropped (the wizard would render a
   broken select).
3. Items with type not in the whitelist are dropped (defaults to
   text would silently change UX).
4. If after filtering we have <3 valid items, the endpoint returns
   500 instead of pretending success — same threshold as before
   the cleaner ran.
5. Client tracks a generation token and ignores responses that
   arrived after a newer generation was started.
"""
from __future__ import annotations
import inspect


def test_phase5_drops_items_with_empty_question():
    """Source-level: the cleaner must skip items whose `question`
    field is empty/whitespace after trim — those would render as
    blank wizard questions."""
    from server import api_wizard_generate_phase5
    src = inspect.getsource(api_wizard_generate_phase5)
    # Look for a guard that skips empty questions inside the
    # cleaning loop. Either `if not q_text: continue` style or
    # filtering in the comprehension.
    has_guard = (
        'if not _q_text' in src
        or 'if not q_text' in src
        or 'q_text:' in src
        or '_skip_phase5_item' in src
    )
    assert has_guard, (
        "BRAIN-69 regression: phase-5 cleaner must drop items "
        "whose `question` field is empty after trim. Otherwise "
        "the AI emitting a `{question: ''}` item silently puts a "
        "blank question into the wizard flow."
    )


def test_phase5_drops_select_items_without_options():
    """Select types with no options are unrenderable — must be
    dropped, not passed through with `options: []`."""
    from server import api_wizard_generate_phase5
    src = inspect.getsource(api_wizard_generate_phase5)
    # The fix should reference both 'single_select' / 'multi_select'
    # AND the options length check. Anchor on `options` + `select`.
    assert "single_select" in src and "multi_select" in src, (
        "BRAIN-69 regression: cleaner must explicitly handle "
        "single_select / multi_select option-presence checks."
    )
    # Look for an option-count guard near the type discrimination.
    # Either `len(opts) < 2` or `not opts` style.
    has_opt_guard = (
        "len(opts)" in src
        or "not opts" in src
        or "_opts_ok" in src
    )
    assert has_opt_guard, (
        "BRAIN-69 regression: cleaner must reject select items "
        "with no/empty options. A `select` rendered with [] would "
        "give the user a Continue button and nothing to pick → "
        "wizard advances with no answer captured."
    )


def test_phase5_returns_500_when_too_few_valid_items_after_filter():
    """If filtering drops items below the 3-question threshold,
    must return 500 (not a partial success). Same threshold as
    pre-cleaner check."""
    from server import api_wizard_generate_phase5
    src = inspect.getsource(api_wizard_generate_phase5)
    # The guard that looked at len(questions) >= 3 originally —
    # must still apply AFTER filtering, not before. Look for a
    # post-filter length check.
    has_post_filter_check = (
        "len(cleaned)" in src and ">= 3" in src
    ) or (
        "len(_cleaned)" in src and ">= 3" in src
    )
    assert has_post_filter_check, (
        "BRAIN-69 regression: cleaner must check len(cleaned) >= 3 "
        "AFTER dropping malformed items. Pre-fix, the threshold was "
        "evaluated on the raw AI output before filtering, so a "
        "5-item response with 4 malformed ones would still pass "
        "the gate and return 1 useful question + 4 placeholders."
    )


def test_client_phase5_response_guarded_by_generation_token():
    """The client's phase-5 fetch handler must drop responses
    that arrive after a newer generation has started — otherwise
    Re-train mid-generation can mix stale + fresh questions in
    _BRAIN_QUESTIONS."""
    with open("templates/jarvis.html", "r", encoding="utf-8") as fh:
        src = fh.read()
    # Anchor on the unique phase-5 endpoint string — there's only
    # one client-side caller.
    # Anchor on the actual fetch call, not earlier comment mentions.
    idx = src.find("fetch('/api/wizard/generate-phase5'")
    assert idx != -1
    # Token is set ~20 lines BEFORE the fetch URL (in the closure
    # setup), guard checks run after the fetch. Widen.
    block = src[max(0, idx - 1500):idx + 5000]
    assert "_brainPhase5Seq" in block, (
        "BRAIN-69 regression: phase-5 fetch must stamp a generation "
        "token (`_brainPhase5Seq`) and bail early in the response "
        "handler if a newer generation has started. Without it, "
        "Re-train mid-generation produces a mixed stale+fresh "
        "phase-5 array."
    )


def test_client_phase5_ignores_response_on_token_mismatch():
    """The handler must compare its captured token against the
    latest sequence and `return` BEFORE pushing into
    `_BRAIN_QUESTIONS`."""
    import re as _re
    with open("templates/jarvis.html", "r", encoding="utf-8") as fh:
        src = fh.read()
    idx = src.find("fetch('/api/wizard/generate-phase5'")
    assert idx != -1
    block = src[max(0, idx - 1500):idx + 5000]
    # The pattern: an `if (_myP5Tok !== window._brainPhase5Seq) return`
    # before the push loop.
    pattern = _re.compile(
        r"if\s*\(\s*_myP5Tok\s*!==\s*window\._brainPhase5Seq\s*\)\s*\{?[^}]*return",
        _re.DOTALL,
    )
    assert pattern.search(block), (
        "BRAIN-69 regression: phase-5 handler must compare token "
        "and `return` early on mismatch BEFORE mutating "
        "_BRAIN_QUESTIONS. Falling through into the push loop "
        "would defeat the guard."
    )
