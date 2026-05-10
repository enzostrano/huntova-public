"""Regression test for CHAT-1: chat dispatcher's set_lead_status branch
must require independent user-message intent confirmation before
mutating a lead's status.

Same prompt-injection class as BRAIN-55/56/57 (delete_lead, mint_share,
update_settings, update_icp) — AI tool calls can be hijacked by
indirect prompt injection in lead notes / scraped page text / inbox
replies that flow back through the chat context. Lead status drives
the entire follow-up sequence + Pulse reporter + DNA training signal,
so a hijacked call that flips a hot lead to "lost" or a cold lead to
"won" silently corrupts the user's pipeline.

Per the autonomous chat-dispatcher security sweep — analogous to the
BRAIN-55..57 audit pattern.
"""
from __future__ import annotations
import inspect


def test_set_lead_status_branch_checks_user_message_intent():
    """The set_lead_status handler must verify the user's current
    message expresses status-change intent, not just trust the AI's
    parsed action."""
    from server import api_chat
    src = inspect.getsource(api_chat)
    idx = src.find('act == "set_lead_status"')
    assert idx != -1, "set_lead_status branch missing — test stale?"
    # 1500 chars after the branch should mention msg-based intent check.
    region = src[idx:idx + 1500]
    assert "msg" in region and ("intent" in region.lower() or "_intent_match" in region), (
        "CHAT-1 regression: set_lead_status must verify the user's "
        "current message contains a status-change intent keyword "
        "before honoring the AI's parsed action. AI tool calls are "
        "hijackable via prompt injection."
    )


def test_set_lead_status_rejection_path_explains():
    """When intent is missing, the branch must return a refusal that
    explains why and how to retry, not silently fail."""
    from server import api_chat
    src = inspect.getsource(api_chat)
    idx = src.find('act == "set_lead_status"')
    region = src[idx:idx + 2000]
    assert ("won't change" in region.lower()
            or "won't update" in region.lower()
            or "won't set" in region.lower()
            or "explicit" in region.lower()), (
        "CHAT-1 regression: missing-intent branch must return a "
        "refusal that asks the user to retry with explicit phrasing, "
        "not fall through silently."
    )
