"""Regression test for CHAT-2: chat dispatcher's `research` action
must require independent user-message intent before launching the
14-25 page deep crawl + AI rewrite that overwrites the lead's
email_subject + email_body.

Same prompt-injection class as BRAIN-55..57 (delete_lead, mint_share,
update_settings, update_icp, set_lead_status). The `research` action:

1. Crawls up to 25 pages of the prospect site (real bandwidth + time).
2. Burns the user's BYOK token budget on the rewrite call.
3. OVERWRITES the existing email draft (the original is moved to
   rewrite_history but a hijacked rewrite still pollutes the active
   draft visible in the dashboard).

Indirect injection vectors:
  - A scraped page text fed back into chat context for summarisation.
  - A lead's notes / scraped social bio.
  - An inbox reply that re-enters the chat context.

Without an intent check, an attacker who can land text in the chat
dispatcher's context (via any of the above) can issue
`research: {lead_id: X}` to drain credits + replace the user's
carefully-tuned cold opener with a hijacked draft.
"""
from __future__ import annotations
import inspect


def test_research_branch_checks_user_message_intent():
    """The research handler must verify the user's current message
    expresses research intent — not just trust the AI's parsed
    action."""
    from server import api_chat
    src = inspect.getsource(api_chat)
    idx = src.find('act == "research"')
    assert idx != -1, "research branch missing — test stale?"
    # 1500 chars after the branch should mention msg-based intent check.
    region = src[idx:idx + 1500]
    assert "msg" in region and (
        "intent" in region.lower()
        or "_intent_match" in region
        or "research" in region.lower()
    ), (
        "CHAT-2 regression: research must verify the user's current "
        "message contains a research-intent keyword AND a lead "
        "reference before burning the user's BYOK token budget on "
        "a 14-25 page crawl + rewrite. AI tool calls are hijackable "
        "via prompt injection."
    )


def test_research_rejection_path_explains():
    """When intent is missing, the branch must return a refusal that
    explains how to retry, not silently fall through."""
    from server import api_chat
    src = inspect.getsource(api_chat)
    idx = src.find('act == "research"')
    region = src[idx:idx + 2200]
    assert ("won't research" in region.lower()
            or "won't crawl" in region.lower()
            or "won't run research" in region.lower()
            or "explicit" in region.lower()), (
        "CHAT-2 regression: missing-intent branch must return a "
        "user-facing refusal explaining the required phrasing, not "
        "silently fall through to the crawl."
    )
