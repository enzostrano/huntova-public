"""Regression test for BRAIN-10 (a371): /api/wizard/complete had no
_check_ai_rate guard. Same omission as BRAIN-5 (a365 fixed it for
generate-phase5). Double-click on the Complete-training button
fired two atomic merge_settings, two background DNA generations
(BYOK spend × 2), two team-default seeds, two master-settings
updates. Per GPT-5.4's audit on idempotency.

Source-level test, same pattern as a365.
"""
from __future__ import annotations
import inspect


def _handler_source() -> str:
    from server import api_wizard_complete
    return inspect.getsource(api_wizard_complete)


def test_complete_handler_calls_check_ai_rate():
    src = _handler_source()
    assert "_check_ai_rate(" in src, (
        "BRAIN-10 regression: /api/wizard/complete must call "
        "_check_ai_rate before doing the atomic merge + kicking off "
        "background DNA generation. Otherwise a double-click costs "
        "the user 2× BYOK AI spend and races the DNA write."
    )


def test_complete_rate_limit_runs_before_merge_settings():
    """The guard must run BEFORE the heavy work (merge_settings call,
    background task spawning). Otherwise we've already paid the cost
    by the time the limiter would reject."""
    src = _handler_source()
    rate_idx = src.find("_check_ai_rate(")
    merge_idx = src.find("db.merge_settings(")
    assert rate_idx != -1, "guard call missing entirely"
    assert merge_idx != -1, "merge_settings call missing — test stale?"
    assert rate_idx < merge_idx, (
        "BRAIN-10 regression: _check_ai_rate must run BEFORE "
        "db.merge_settings, otherwise the heavy mutator + AI "
        "background task have already been triggered."
    )
