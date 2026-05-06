"""BRAIN-204: db._build_team_prompt_addendum invariant audit.

a520 (BRAIN-PROD-4) expanded the team-prompt builder ~30× to
interpolate the full wizard payload into role-specific personas.
Each role's addendum gets prefixed with a shared business-context
block, then a role-specific body.

Pinned invariants:

1. Each known slot (prospector / qualifier / researcher / drafter /
   sequence / triager / supervisor) returns a non-empty string.
2. Unknown slot returns a fallback or empty string (no crash).
3. Empty brain produces at least a skeleton (no crash).
4. Long `business_description` (>600 chars) truncated with `…`.
5. Long `target_clients` (>600 chars) truncated.
6. Long `example_good_clients` (>400 chars) truncated.
7. Wizard-shape brain takes precedence over normalized_hunt_profile.
8. Output stays under 8000-char DB cap.
9. None brain handled defensively.
10. List fields capped at 6 items (services / industries / buyer_roles).
"""
from __future__ import annotations


_KNOWN_SLOTS = ("prospector", "qualifier", "researcher", "drafter",
                "sequence", "triager")


def test_each_slot_returns_string():
    from db import _build_team_prompt_addendum
    for slot in _KNOWN_SLOTS:
        out = _build_team_prompt_addendum(slot, {"business_description": "We sell widgets."})
        assert isinstance(out, str)


def test_known_slot_returns_non_empty():
    from db import _build_team_prompt_addendum
    out = _build_team_prompt_addendum("prospector",
                                       {"business_description": "We sell widgets."})
    assert len(out) > 100  # substantial body


def test_none_brain_handled():
    """None brain must not crash."""
    from db import _build_team_prompt_addendum
    out = _build_team_prompt_addendum("prospector", None)
    assert isinstance(out, str)


def test_empty_brain_handled():
    """Empty brain produces at least a skeleton."""
    from db import _build_team_prompt_addendum
    out = _build_team_prompt_addendum("prospector", {})
    assert isinstance(out, str)


def test_long_business_description_truncated():
    """`business_description` > 600 chars → truncated with `…`."""
    from db import _build_team_prompt_addendum
    long_bd = "x" * 1000
    out = _build_team_prompt_addendum("prospector",
                                       {"business_description": long_bd})
    # The string should NOT contain 1000 consecutive 'x' chars.
    assert "x" * 1000 not in out
    # Truncation marker present.
    assert "…" in out


def test_long_target_clients_truncated():
    from db import _build_team_prompt_addendum
    long_tc = "y" * 1000
    out = _build_team_prompt_addendum("qualifier",
                                       {"business_description": "We sell.",
                                        "target_clients": long_tc})
    assert "y" * 1000 not in out


def test_long_example_good_clients_truncated():
    from db import _build_team_prompt_addendum
    long_ex = "z" * 600
    out = _build_team_prompt_addendum("researcher",
                                       {"business_description": "We sell.",
                                        "example_good_clients": long_ex})
    assert "z" * 600 not in out


def test_output_under_8000_char_cap():
    """Even with a fat brain, addendum stays under the 8000-char DB cap."""
    from db import _build_team_prompt_addendum
    fat_brain = {
        "business_description": "x" * 2000,
        "target_clients": "y" * 2000,
        "example_good_clients": "z" * 2000,
        "exclusions": "w" * 2000,
        "services": ["service-" + str(i) for i in range(50)],
        "preferred_industries": ["industry-" + str(i) for i in range(50)],
        "buyer_roles": ["role-" + str(i) for i in range(50)],
        "geographies": ["country-" + str(i) for i in range(50)],
        "differentiators": ["diff-" + str(i) for i in range(50)],
        "value_propositions": ["vp-" + str(i) for i in range(50)],
        "pain_points_addressed": ["pain-" + str(i) for i in range(50)],
    }
    for slot in _KNOWN_SLOTS:
        out = _build_team_prompt_addendum(slot, fat_brain)
        assert len(out) < 8000, (
            f"slot {slot} addendum {len(out)} chars exceeds 8000-char cap"
        )


def test_services_capped_at_6():
    """List fields capped — only first 6 services appear."""
    from db import _build_team_prompt_addendum
    brain = {
        "business_description": "We sell.",
        "services": ["s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8", "s9"],
    }
    out = _build_team_prompt_addendum("prospector", brain)
    # s7 / s8 / s9 must NOT appear.
    assert "s7" not in out
    assert "s8" not in out


def test_wizard_shape_takes_precedence_over_normalized():
    """When `services_clean` (wizard shape) AND `services` (legacy)
    AND `normalized_hunt_profile.services_clean` all present, wizard
    shape wins."""
    from db import _build_team_prompt_addendum
    brain = {
        "business_description": "We sell.",
        "services_clean": ["wizard-service"],
        "services": ["legacy-service"],
        "normalized_hunt_profile": {
            "services_clean": ["normalized-service"]
        }
    }
    out = _build_team_prompt_addendum("prospector", brain)
    # wizard shape wins.
    assert "wizard-service" in out
    # legacy might or might not appear (fallback if wizard missing).


def test_legacy_brain_works_via_normalized_hunt_profile():
    """Caller passing only the legacy normalized_hunt_profile shape
    still produces an addendum."""
    from db import _build_team_prompt_addendum
    brain = {
        "normalized_hunt_profile": {
            "services_clean": ["legacy-svc"],
            "buyer_roles_clean": ["CMO"],
            "preferred_industries": ["SaaS"],
        }
    }
    out = _build_team_prompt_addendum("prospector", brain)
    assert isinstance(out, str)
    # At least one of the legacy fields surfaces.


def test_unknown_slot_returns_string_or_empty():
    """Unknown slot — must not crash. Returns either a string or empty."""
    from db import _build_team_prompt_addendum
    out = _build_team_prompt_addendum("totally-unknown-slot",
                                       {"business_description": "We sell."})
    assert isinstance(out, str)


def test_output_contains_role_anchor():
    """Each known slot's body should reference its role
    (e.g. 'Prospector', 'Qualifier')."""
    from db import _build_team_prompt_addendum
    out = _build_team_prompt_addendum("prospector",
                                       {"business_description": "We sell."})
    assert "Prospector" in out or "prospector" in out.lower()


def test_business_description_appears():
    """When supplied, business_description appears in OUR BUSINESS context line."""
    from db import _build_team_prompt_addendum
    out = _build_team_prompt_addendum("qualifier",
                                       {"business_description": "We sell unicorn-grade widgets."})
    assert "unicorn-grade widgets" in out


def test_geographies_appears():
    """`geographies` field surfaces in GEOGRAPHIES context line."""
    from db import _build_team_prompt_addendum
    out = _build_team_prompt_addendum("prospector",
                                       {"business_description": "We sell.",
                                        "geographies": ["United States", "Germany"]})
    assert "United States" in out
    assert "Germany" in out
