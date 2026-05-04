"""Regression test for BRAIN-6 (a367): /api/wizard/save-progress
was unconditionally setting `_wizard_answers = answers`. An empty
`answers={}` from any race (Skip fires before resume populates
client state, stale fresh-state tab, buggy client) silently wiped
all prior answers. The fix introduces _merge_wizard_answers — a
small pure helper that merges instead of replaces, with empty-
incoming as a no-op.

Pure unit test. The helper has no DB / network dependencies.
"""
from __future__ import annotations


def test_empty_incoming_is_noop():
    from server import _merge_wizard_answers
    prev = {"company_name": "Acme", "services": ["a", "b"]}
    assert _merge_wizard_answers(prev, {}) == prev, (
        "BRAIN-6 regression: empty incoming must NOT wipe prior "
        "answers. Was unconditionally replacing — any stale request "
        "with empty answers clobbered all saved data."
    )


def test_none_incoming_is_noop():
    from server import _merge_wizard_answers
    prev = {"company_name": "Acme"}
    assert _merge_wizard_answers(prev, None) == prev


def test_full_incoming_merges_with_prev():
    from server import _merge_wizard_answers
    prev = {"company_name": "Acme", "services": ["a"]}
    incoming = {"target_clients": "SMBs", "buyer_roles": ["CEO"]}
    result = _merge_wizard_answers(prev, incoming)
    assert result == {
        "company_name": "Acme",
        "services": ["a"],
        "target_clients": "SMBs",
        "buyer_roles": ["CEO"],
    }


def test_incoming_overwrites_on_collision():
    """When the same key is in both, incoming wins — that's how the
    user expresses intent to revise an answer."""
    from server import _merge_wizard_answers
    prev = {"company_name": "OldName"}
    incoming = {"company_name": "NewName"}
    assert _merge_wizard_answers(prev, incoming) == {"company_name": "NewName"}


def test_non_dict_prev_treated_as_empty():
    """Defensive: legacy/corrupt rows could have _wizard_answers
    as None or a string. Don't raise — coerce to {} and merge."""
    from server import _merge_wizard_answers
    incoming = {"company_name": "Acme"}
    assert _merge_wizard_answers(None, incoming) == incoming
    assert _merge_wizard_answers("garbage", incoming) == incoming
    assert _merge_wizard_answers([], incoming) == incoming


def test_non_dict_incoming_is_noop():
    """Defensive: a buggy client sends a list or string as `answers`.
    Treat as empty incoming → don't clobber prev."""
    from server import _merge_wizard_answers
    prev = {"company_name": "Acme"}
    assert _merge_wizard_answers(prev, "garbage") == prev
    assert _merge_wizard_answers(prev, []) == prev
    assert _merge_wizard_answers(prev, 42) == prev
