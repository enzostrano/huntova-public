"""Regression test for CHAT-4: chat dispatcher's `sequence_run`
action with dry_run=False (live cold-email blast) must require
independent user-message intent.

Same prompt-injection class as BRAIN-55..57 + CHAT-1/2/3 — but
this is the HIGHEST-stakes action in the dispatcher because the
side-effects are:

  1. Irreversible (cold-email sends can't be unsent).
  2. Touch the user's deliverability reputation (sender score,
     domain trust, ESP feedback loops).
  3. Touch the prospect's inbox (the user's brand at risk).

Indirect injection vectors include lead notes, scraped page
text, and inbox-reply summaries that re-enter the chat
dispatcher. Pre-fix the only barrier was "the AI agreed",
which is hijackable by design.

Low-trust transports (Telegram bridge) already required
dry_run=True (server.py:2945-2951). Trusted sources (web
dashboard, CLI) had no equivalent gate before this fix.
"""
from __future__ import annotations
import inspect


def test_sequence_run_branch_checks_user_message_intent():
    """The sequence_run dispatch branch (second occurrence in source —
    the first is the low-trust dry_run gate above the action table)
    must verify the user's current message expresses send-cadence
    intent before allowing dry_run=False to fire."""
    from server import api_chat
    src = inspect.getsource(api_chat)
    first = src.find('act == "sequence_run"')
    assert first != -1, "sequence_run branch missing — test stale?"
    second = src.find('act == "sequence_run"', first + 1)
    assert second != -1, "sequence_run dispatch branch missing — test stale?"
    region = src[second:second + 1600]
    # Must reference an intent-matching keyword AND tie it to the
    # user's current message (`_msg_low` / `msg`) inside the branch.
    assert (
        ("_intent_match" in region or "intent" in region.lower())
        and ("_msg_low" in region or "msg.lower" in region or "(msg" in region)
    ), (
        "CHAT-4 regression: sequence_run live (dry_run=False) must "
        "verify the user's current message expresses send-cadence "
        "intent (look for _msg_low + intent keyword check). AI "
        "tool calls are hijackable via prompt injection."
    )


def test_sequence_run_rejection_path_explains():
    """Missing-intent + live-send branch must return a refusal that
    explains the required phrasing or suggests dry_run."""
    from server import api_chat
    src = inspect.getsource(api_chat)
    first = src.find('act == "sequence_run"')
    second = src.find('act == "sequence_run"', first + 1)
    region = src[second:second + 2400]
    assert ("won't send" in region.lower()
            or "won't run" in region.lower()
            or "won't fire" in region.lower()
            or "explicit" in region.lower()), (
        "CHAT-4 regression: missing-intent live-send branch must "
        "return a refusal explaining the required phrasing or "
        "suggesting dry_run preview, not silently send."
    )
