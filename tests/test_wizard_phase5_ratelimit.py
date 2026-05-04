"""Regression test for BRAIN-5 (a365): /api/wizard/generate-phase5
was the only wizard AI endpoint missing a `_check_ai_rate` guard.
A double-click on the "Generate phase 5" button (or any chatty
client) fired duplicate AI calls — each cost real spend on the
user's BYOK key. The fix adds the same 2-line guard every sibling
wizard endpoint already had.

This test asserts the guard is present at the source level. Pure
unit test (no TestClient, no auth, no AI mocking) — the invariant
is purely lexical: "the handler must call _check_ai_rate before
doing AI work". If a future refactor removes the guard, this test
fails.
"""
from __future__ import annotations

import inspect


def _handler_source() -> str:
    from server import api_wizard_generate_phase5
    return inspect.getsource(api_wizard_generate_phase5)


def test_phase5_handler_calls_check_ai_rate():
    src = _handler_source()
    assert "_check_ai_rate(" in src, (
        "BRAIN-5 regression: /api/wizard/generate-phase5 must call "
        "_check_ai_rate before any AI work, matching every other "
        "wizard AI endpoint (scan / save-progress / assist)."
    )


def test_phase5_rate_limit_runs_before_ai_work():
    """The guard must run BEFORE the model resolution / prompt build,
    otherwise duplicate calls already pay the AI cost by the time the
    limiter would have rejected them."""
    src = _handler_source()
    rate_idx = src.find("_check_ai_rate(")
    model_idx = src.find("_get_model_for_user(")
    assert rate_idx != -1, "guard call missing entirely"
    assert model_idx != -1, "model resolution call missing — test stale?"
    assert rate_idx < model_idx, (
        "BRAIN-5 regression: _check_ai_rate must run BEFORE "
        "_get_model_for_user, otherwise the AI call has already been "
        "set up by the time the rate limit would reject."
    )


def test_phase5_returns_429_shape_on_rate_hit():
    """Verify the 429 branch returns the same shape the frontend's
    error-toast path already handles for the other wizard endpoints
    (`{"error": "..."}` body + 429 status)."""
    src = _handler_source()
    # crude check: if guard exists, it should mention status_code=429
    if "_check_ai_rate(" in src:
        guard_block = src[src.find("_check_ai_rate("):src.find("_check_ai_rate(") + 400]
        assert "429" in guard_block, (
            "rate-limit branch must return HTTP 429 — frontend error "
            "handlers key off this status code"
        )
