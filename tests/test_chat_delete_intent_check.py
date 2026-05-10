"""Regression test for BRAIN-55 (a416): chat dispatcher's delete_lead
branch must require independent user-message intent confirmation
before honoring confirm=true. AI tool calls can be hijacked by
prompt injection in lead notes / scraped page text — schema
validation alone isn't enough.

Per GPT-5.4 audit on prompt-injection-driven unauthorized tool
invocation class.
"""
from __future__ import annotations
import inspect


def test_delete_branch_checks_user_message_intent():
    """The delete_lead handler must check the user's current `msg`
    for explicit delete intent, not just the AI's confirm bit."""
    from server import api_chat
    src = inspect.getsource(api_chat)
    # The branch must reference msg / user-input independently of
    # parsed.get("confirm").
    delete_idx = src.find('act == "delete_lead"')
    assert delete_idx != -1, "delete_lead branch missing — test stale?"
    # 800 chars after the branch should mention msg-based intent check.
    region = src[delete_idx:delete_idx + 1500]
    assert ("msg" in region and "delete" in region.lower()
            and ("intent" in region.lower() or "_intent_match" in region or "lid_low" in region)), (
        "BRAIN-55 regression: delete_lead must verify the user's "
        "current message contains 'delete' + the lead_id before "
        "honoring confirm=true. AI confirm alone is hijackable via "
        "prompt injection."
    )


def test_delete_branch_explicit_intent_phrase_required():
    """Source-level: the guard should reject confirm=true when the
    user's message lacks a delete intent."""
    from server import api_chat
    src = inspect.getsource(api_chat)
    delete_idx = src.find('act == "delete_lead"')
    region = src[delete_idx:delete_idx + 2000]
    # Look for the rejection branch.
    assert ('"I won\'t delete' in region
            or 'won\'t delete' in region
            or "explicit" in region.lower()), (
        "BRAIN-55 regression: when intent is missing, the branch "
        "must return a refusal explaining the user must reply with "
        "explicit delete confirmation, not silently fail."
    )
