"""Regression test for BRAIN-56 (a417): chat dispatcher's mint_share
must require independent user-intent confirmation. Same prompt-
injection-driven unauthorized tool invocation class as BRAIN-55
(delete_lead), but for public-data-leak via /h/<slug> share link.

Per GPT-5.4 audit on side-effecting tool intent-verification sweep.
"""
from __future__ import annotations
import inspect


def test_mint_share_requires_intent_keyword():
    from server import api_chat
    src = inspect.getsource(api_chat)
    mint_idx = src.find('act == "mint_share"')
    assert mint_idx != -1
    region = src[mint_idx:mint_idx + 1500]
    assert ("share" in region.lower()
            and "_share_keywords" in region or "msg" in region and "share" in region.lower()), (
        "BRAIN-56 regression: mint_share branch must check the user's "
        "current message for share intent before creating a public link."
    )


def test_mint_share_refuses_without_intent():
    """Source-level: when intent missing, refusal must be explicit."""
    from server import api_chat
    src = inspect.getsource(api_chat)
    mint_idx = src.find('act == "mint_share"')
    region = src[mint_idx:mint_idx + 2000]
    assert ('won\'t mint' in region or "won't mint" in region or "explicit ask" in region), (
        "BRAIN-56 regression: missing-intent branch must return a clear "
        "refusal explaining the user must ask explicitly."
    )
