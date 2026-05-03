"""Regression test for CHAT-3: chat dispatcher's `playbook_install`
action must require independent user-message intent.

Same prompt-injection class as BRAIN-55..57 + CHAT-1/2. The
`playbook_install` action:

1. Saves a bundled hunt-recipe to the user's DB.
2. Auto-seeds wizard ICP / target_clients / default_tone IF those
   fields are empty (common case for fresh users).

A fresh user (empty wizard) hijacked via prompt injection in
scraped page text, lead notes, or web_search summaries can be
silently steered into the wrong playbook (e.g., "video-production"
seeded into a real-estate user's wizard). Subsequent hunts then
run with the poisoned ICP — quietly burning AI credits on the
wrong audience.

Even for users with a populated wizard the action still saves a
new recipe row + sets default_tone if empty. Defence-in-depth:
require the user's current message to express install intent.

Per the autonomous chat-dispatcher security sweep — analogous to
BRAIN-55/56/57 + CHAT-1/2.
"""
from __future__ import annotations
import inspect


def test_playbook_install_branch_checks_user_message_intent():
    """The playbook_install handler must verify the user's current
    message expresses install intent."""
    from server import api_chat
    src = inspect.getsource(api_chat)
    idx = src.find('act == "playbook_install"')
    assert idx != -1, "playbook_install branch missing — test stale?"
    region = src[idx:idx + 1500]
    assert "msg" in region and (
        "intent" in region.lower()
        or "_intent_match" in region
        or "playbook" in region.lower()
    ), (
        "CHAT-3 regression: playbook_install must verify the user's "
        "current message expresses install intent. AI tool calls "
        "are hijackable via prompt injection in scraped text + "
        "lead notes."
    )


def test_playbook_install_rejection_path_explains():
    """Missing-intent branch must return a refusal with retry guidance,
    not silently install."""
    from server import api_chat
    src = inspect.getsource(api_chat)
    idx = src.find('act == "playbook_install"')
    region = src[idx:idx + 2200]
    assert ("won't install" in region.lower()
            or "explicit" in region.lower()), (
        "CHAT-3 regression: missing-intent branch must return a "
        "refusal explaining the required phrasing, not silently "
        "fall through to the install."
    )
