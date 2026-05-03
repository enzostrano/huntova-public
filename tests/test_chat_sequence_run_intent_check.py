"""Regression test for CHAT-4: chat dispatcher's `sequence_run`
action with dry_run=False (live send) must require independent
user-message intent.

Same prompt-injection class as BRAIN-55..57 + CHAT-1/2/3. The
`sequence_run` action with `dry_run=False`:

  - SENDS REAL COLD EMAILS via the configured SMTP transport.
  - Bumps `_seq_step` on each lead, advancing the cadence.
  - Stamps activity timestamps that drive Pulse + DNA training.

This is the highest-stakes action in the dispatcher: irreversible
side-effects that touch the user's deliverability reputation +
the prospect's inbox. A prompt injection in lead notes, scraped
page text, or inbox-reply summaries that hands a `sequence_run
{dry_run: false}` to the AI causes a live blast — uninitiated by
the user, unrecoverable.

Low-trust transports (Telegram bridge) already require dry_run
(server.py:2945-2951). Trusted sources (web dashboard, CLI) get
no equivalent gate — add one.
"""
from __future__ import annotations
import inspect


def test_sequence_run_branch_checks_user_message_intent():
    """The sequence_run handler must verify the user's current
    message expresses send-cadence intent before going live.

    The dispatch branch lives at the SECOND `act == "sequence_run"`
    occurrence in source (the first is the low-trust dry_run gate
    above the action dispatch table). Inspect that branch's
    source span and look for an explicit intent check that ties
    to the user-message variable `msg` (not `_chat_source`)."""
    from server import api_chat
    src = inspect.getsource(api_chat)
    # Find the dispatch branch (second occurrence, NOT the
    # low-trust gate that sits earlier in the function).
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
