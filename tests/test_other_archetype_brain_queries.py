"""Regression test for BRAIN-16 (a377): mid-hunt batch query
generation skipped brain templates when archetype="other", even
though _generate_brain_queries still produces useful role/
industry/example-client queries via its common sections.

Same silent-downgrade class as BRAIN-15 (a376) — different
location. Per GPT-5.4 audit pattern: hunt gate treats partial /
classification-uncertain state as "missing" and silently
substitutes weaker defaults.
"""
from __future__ import annotations
import inspect


def test_other_archetype_brain_still_produces_queries():
    """The function works for archetype='other' — its common
    sections (role-based, example-client, directory queries) run
    regardless of archetype branch. So gating the call on
    archetype != 'other' is a bug at the CALL SITE, not the
    function."""
    from app import _generate_brain_queries
    brain = {
        "hunt_brain_version": 1,
        "archetype": "other",
        "services_clean": ["custom development"],
        "preferred_industries": ["fintech", "edtech"],
        "buyer_roles_clean": ["CTO"],
        "triggers_clean": ["scaling team"],
        "ideal_company_size": ["50-200"],
        "example_good_clients": [{"name": "Acme", "reason": ""}],
    }
    queries = _generate_brain_queries(brain, ["UK"])
    assert isinstance(queries, list)
    assert len(queries) > 0, (
        "BRAIN-16: _generate_brain_queries must produce queries even "
        "when archetype='other' (via its common role/example/directory "
        "sections). The bug is the call-site gate skipping it."
    )


def test_mid_hunt_batch_does_not_gate_brain_on_archetype():
    """Source-level: the mid-hunt regen branch must NOT condition
    brain-template usage on archetype != 'other'. That gate
    silently downgrades 'other'-classified users to structured
    fallback for every batch from 2 onwards."""
    import app
    src = inspect.getsource(app)
    # Find the mid-hunt regen marker — comment includes "Generate next batch"
    region_start = src.find("# Generate next batch using brain")
    assert region_start != -1, "regen marker not found — test stale?"
    region = src[region_start:region_start + 600]
    assert 'archetype") != "other"' not in region, (
        "BRAIN-16 regression: mid-hunt batch query regeneration "
        "must NOT gate brain templates on `archetype != \"other\"` — "
        "_generate_brain_queries handles 'other' archetype via its "
        "common sections and produces useful queries."
    )
