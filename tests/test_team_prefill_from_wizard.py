"""Regression tests for BRAIN-PROD-4 (a520): team specialists were
seeded with one-liner prompt_addendums (~30 words each) that ignored
most of the wizard answers. The user reported "Team of agents in
settings should be prefilled with what he understood from wizard and
accordingly prefilled it 30x current text to train the agents
perfectly".

Fix expanded `_build_team_prompt_addendum` to interpolate the FULL
wizard payload (business_description, target_clients, outreach_tone,
value_propositions, differentiators, pain_points_addressed,
example_good_clients, exclusions) on top of the structured lists, and
widened `_team_brain_for` to pass the merged wizard+brain payload.
The prompt_addendum DB cap was widened from 4000 to 8000 chars to fit
the new richer prompts.

These tests assert:
1. Each role's prompt is now substantially longer (>=200 words).
2. The prompt actually pulls from the rich wizard fields.
3. The seeder writes those addendums into the DB rows.
4. The 8000-char cap accommodates the new richer text.
5. Empty wizard input falls back gracefully without raising.
"""
from __future__ import annotations

import asyncio


_FULL_BRAIN = {
    "business_description": (
        "We are a 12-person growth consultancy that helps Series A and "
        "Series B B2B SaaS founders fix their go-to-market motion in "
        "the first 90 days. We embed with the founder, run a 30-60-90 "
        "diagnostic, and ship a working outbound and content motion."
    ),
    "target_clients": (
        "Series A and Series B B2B SaaS companies, 15-60 employees, "
        "1M-8M ARR, founder-led sales, no in-house growth team. "
        "Strongest fit when founders are technical, post-PMF but pre-"
        "marketing-hire, and burned by a freelancer or agency in the "
        "previous 12 months."
    ),
    "services_clean": [
        "Outbound system design",
        "Content engine",
        "ICP refinement",
        "Sales playbook",
        "Funnel diagnostics",
    ],
    "preferred_industries": ["B2B SaaS", "DevTools", "Fintech", "LegalTech"],
    "buyer_roles_clean": ["Founder", "CEO", "Head of Growth", "Head of Marketing"],
    "geographies": ["United States", "United Kingdom", "Canada", "Germany"],
    "outreach_tone": "consultative",
    "value_propositions": [
        "First 90 days bring a working outbound system shipping daily",
        "Replace a $20k/mo agency with a $7k/mo embedded operator",
        "Founder keeps the keys - we leave a self-running playbook",
    ],
    "differentiators": [
        "Ex-VP Growth at two acquired SaaS companies on the team",
        "Embedded model - we sit in your Slack, not behind a portal",
        "Fixed scope, fixed price - no hourly bill creep",
    ],
    "pain_points_addressed": [
        "Outbound that limps along at 0.5% reply rate",
        "Content that's all founder-noise, no buyer-pull",
        "ICP drift - selling to whoever picks up the phone",
    ],
    "example_good_clients": (
        "Acme DevTools (Series A, 22 people, devtool for SREs); "
        "Northwind Fintech (Series B, 45 people, B2B finance ops); "
        "Pioneer Legal (Series A, 18 people, contract automation)."
    ),
    "exclusions": (
        "No enterprise (>500 employees), no government / public sector, "
        "no agencies that compete with growth consultancies, no pre-"
        "revenue companies, no consumer / B2C, no markets outside our "
        "named geographies."
    ),
}

_ALL_SLOTS = (
    "prospector", "qualifier", "researcher", "contact_finder",
    "outreach_drafter", "inbox_triager", "sequence_op", "pulse_reporter",
)


def _word_count(s: str) -> int:
    return len((s or "").split())


# Pure helper tests ---------------------------------------------------

def test_each_role_prompt_is_at_least_200_words():
    """The user explicitly asked for a ~30x expansion of the prior
    one-liners. Sanity-check by asserting each role prompt is at least
    200 words when the wizard is fully populated."""
    from db import _build_team_prompt_addendum
    too_short = []
    for slot in _ALL_SLOTS:
        prompt = _build_team_prompt_addendum(slot, _FULL_BRAIN)
        wc = _word_count(prompt)
        if wc < 200:
            too_short.append((slot, wc))
    assert not too_short, (
        f"BRAIN-PROD-4 regression: roles with <200-word prompts: "
        f"{too_short}. Each role should produce a 200-300 word "
        f"persona-style brief from the full wizard payload."
    )


def test_each_role_prompt_pulls_business_description():
    """The OUR BUSINESS context block should appear in every role's
    prompt when business_description is set."""
    from db import _build_team_prompt_addendum
    bd_marker = "growth consultancy"
    for slot in _ALL_SLOTS:
        prompt = _build_team_prompt_addendum(slot, _FULL_BRAIN)
        assert bd_marker in prompt, (
            f"BRAIN-PROD-4 regression: {slot} prompt does not interpolate "
            f"business_description."
        )


def test_each_role_prompt_pulls_target_clients():
    """target_clients is the canonical ICP statement; every role must
    inherit it so the dispatcher and hunt loop have ground truth."""
    from db import _build_team_prompt_addendum
    tc_marker = "Series A and Series B"
    for slot in _ALL_SLOTS:
        prompt = _build_team_prompt_addendum(slot, _FULL_BRAIN)
        assert tc_marker in prompt, (
            f"BRAIN-PROD-4 regression: {slot} prompt missing "
            f"target_clients context."
        )


def test_each_role_prompt_pulls_exclusions():
    """exclusions is the hard-reject filter; every role needs it."""
    from db import _build_team_prompt_addendum
    excl_marker = "No enterprise"
    for slot in _ALL_SLOTS:
        prompt = _build_team_prompt_addendum(slot, _FULL_BRAIN)
        assert excl_marker in prompt, (
            f"BRAIN-PROD-4 regression: {slot} prompt missing "
            f"exclusions / DO NOT TARGET filter."
        )


def test_outreach_drafter_respects_outreach_tone():
    """The drafter prompt is tone-aware: should mention the tone label
    and the canonical bad phrases to avoid."""
    from db import _build_team_prompt_addendum
    prompt = _build_team_prompt_addendum("outreach_drafter", _FULL_BRAIN)
    assert "consultative" in prompt.lower(), (
        "BRAIN-PROD-4: drafter prompt must reference the wizard's "
        "outreach_tone setting."
    )
    assert ("circling back" in prompt.lower()
            or "synergy" in prompt.lower()
            or "leverage" in prompt.lower()), (
        "BRAIN-PROD-4: drafter prompt should ban canonical filler."
    )


def test_sequence_op_and_triager_inherit_tone():
    """Follow-ups and reply-classification both depend on tone for
    coherence with the opener."""
    from db import _build_team_prompt_addendum
    for slot in ("sequence_op", "inbox_triager"):
        prompt = _build_team_prompt_addendum(slot, _FULL_BRAIN)
        assert "consultative" in prompt.lower(), (
            f"BRAIN-PROD-4: {slot} should reference outreach_tone."
        )


def test_empty_brain_returns_role_body_only():
    """Fresh installs (no wizard yet) must not crash; the addendum
    should still be a non-raising string with the role's own body."""
    from db import _build_team_prompt_addendum
    for slot in _ALL_SLOTS:
        out_none = _build_team_prompt_addendum(slot, None)
        assert isinstance(out_none, str)
        out_empty = _build_team_prompt_addendum(slot, {})
        assert isinstance(out_empty, str)
        # The role-specific body still ships even without context, so
        # the user gets a usable starting prompt to edit.
        assert "ROLE" in out_empty


def test_unknown_slot_returns_empty():
    """Unknown slot names must return '' rather than raising."""
    from db import _build_team_prompt_addendum
    assert _build_team_prompt_addendum("nonexistent_role", _FULL_BRAIN) == ""


def test_prompt_fits_within_8000_char_cap():
    """update_team_member caps prompt_addendum at 8000 chars (a520
    widened from 4000). Seeded prompts must fit."""
    from db import _build_team_prompt_addendum
    for slot in _ALL_SLOTS:
        prompt = _build_team_prompt_addendum(slot, _FULL_BRAIN)
        assert len(prompt) <= 8000, (
            f"BRAIN-PROD-4: {slot} seeded prompt is {len(prompt)} "
            f"chars; over the 8000 DB cap."
        )


def test_legacy_normalized_brain_shape_still_works():
    """Old callers passed only normalized_hunt_profile (services_clean +
    preferred_industries + buyer_roles_clean + geographies). Must
    still produce a usable prompt body."""
    from db import _build_team_prompt_addendum
    legacy = {
        "services_clean": ["A", "B"],
        "preferred_industries": ["SaaS"],
        "buyer_roles_clean": ["CEO"],
        "geographies": ["US"],
    }
    for slot in _ALL_SLOTS:
        prompt = _build_team_prompt_addendum(slot, legacy)
        assert isinstance(prompt, str)
        assert "ROLE" in prompt


# DB-backed tests using the local_env sqlite fixture ------------------

def test_seed_team_defaults_writes_rich_addendums(local_env):
    """End-to-end: seed_team_defaults inserts 8 rows whose
    prompt_addendums are populated from the full wizard payload."""
    async def _run():
        from db import init_db, create_user, seed_team_defaults, list_team
        from auth import hash_password
        await init_db()
        uid = await create_user("test@example.com", hash_password("p"), "T")

        res = await seed_team_defaults(uid, brain=_FULL_BRAIN, overwrite=True)
        assert res["inserted"] == 8

        rows = await list_team(uid)
        assert len(rows) == 8
        short_prompts = []
        for r in rows:
            wc = _word_count(r.get("prompt_addendum", ""))
            if wc < 200:
                short_prompts.append((r["slot"], wc))
        assert not short_prompts, (
            f"BRAIN-PROD-4 regression: seeded rows with <200-word "
            f"prompts: {short_prompts}"
        )
    asyncio.run(_run())


def test_team_brain_for_returns_merged_wizard_payload(local_env):
    """_team_brain_for must surface BOTH the rich paragraph fields
    (business_description, target_clients, outreach_tone, ...) AND
    the structured lists from normalized_hunt_profile."""
    async def _run():
        from db import init_db, create_user, save_settings
        from auth import hash_password
        from server import _team_brain_for
        await init_db()
        uid = await create_user("test@example.com", hash_password("p"), "T")
        await save_settings(uid, {"wizard": _FULL_BRAIN})

        brain = await _team_brain_for(uid)
        assert brain.get("business_description"), (
            "BRAIN-PROD-4: _team_brain_for must return the rich "
            "wizard fields, not just the structured lists."
        )
        assert brain.get("services_clean") or brain.get("services"), (
            "BRAIN-PROD-4: _team_brain_for must still return the "
            "canonical service list."
        )
        assert brain.get("outreach_tone") == "consultative"
        assert brain.get("exclusions")
    asyncio.run(_run())


def test_seed_via_team_brain_for_renders_rich_prompts(local_env):
    """Full integration: save_settings -> _team_brain_for ->
    seed_team_defaults -> list_team. Spot-check role-specific tone +
    business markers in the resulting rows."""
    async def _run():
        from db import init_db, create_user, save_settings, seed_team_defaults, list_team
        from auth import hash_password
        from server import _team_brain_for
        await init_db()
        uid = await create_user("test@example.com", hash_password("p"), "T")
        await save_settings(uid, {"wizard": _FULL_BRAIN})

        brain = await _team_brain_for(uid)
        res = await seed_team_defaults(uid, brain=brain, overwrite=True)
        assert res["inserted"] == 8

        rows = await list_team(uid)
        by_slot = {r["slot"]: r for r in rows}
        assert "consultative" in by_slot["outreach_drafter"]["prompt_addendum"].lower()
        assert "growth consultancy" in by_slot["qualifier"]["prompt_addendum"]
        # Reseed-scenario sanity: a second call with overwrite=True
        # must replace prior addendums and not raise.
        await seed_team_defaults(uid, brain=brain, overwrite=True)
        rows2 = await list_team(uid)
        assert len(rows2) == 8
    asyncio.run(_run())
