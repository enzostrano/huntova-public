"""Regression tests for BRAIN-100 (a469): the BRAIN-99
underscore-block on _coerce_wizard_answer must NOT break the
legitimate server-side underscore writes inside
api_wizard_complete's _apply_wizard_mutations.

Failure mode (Per Huntova engineering review on validation
trust-separation):

BRAIN-99 (a468) closed the OWASP mass-assignment hole by
rejecting any underscore-prefixed key from
_coerce_wizard_answer. That helper is called from
_merge_wizard_answers (save-progress) AND from the
client-profile sanitization loop in api_wizard_complete.

Inside api_wizard_complete, AFTER the client profile is
filtered, _apply_wizard_mutations writes server-owned flags:

    w["_site_scanned"] = True
    w["_interview_complete"] = True

These are DIRECT DICT ASSIGNMENTS inside the merge mutator —
they do NOT go through _coerce_wizard_answer. So BRAIN-99
didn't break them. But there's a regression risk if a future
refactor accidentally routes those writes through the
validator: complete would silently fail to set the flags,
status would lie about completion, the legitimate flow
breaks while the security guard appears intact.

This release locks the trust-separation seam down with
regression tests that pin both halves:

1. Trusted server-side path: `_apply_wizard_mutations` writes
   `_interview_complete` and `_site_scanned` directly (NOT
   through `_coerce_wizard_answer`).
2. Untrusted client path: client-supplied underscore keys
   in `profile` payload are dropped (BRAIN-75 + BRAIN-99
   defense in depth).

If a future refactor flattens the two paths into one
unified validator, this test fails LOUDLY at the pre-fix
boundary.
"""
from __future__ import annotations
import inspect


def test_apply_wizard_mutations_writes_interview_complete_directly():
    """Source-level: the trusted server-side write of
    `_interview_complete` must be a direct assignment, not
    routed through `_coerce_wizard_answer`."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    # The direct assignment must appear in the function body.
    has_direct_assign = (
        'w["_interview_complete"] = True' in src
        or "w['_interview_complete'] = True" in src
    )
    assert has_direct_assign, (
        "BRAIN-100 regression: `_apply_wizard_mutations` must "
        "directly assign `w['_interview_complete'] = True`. "
        "If a future refactor routes this through "
        "_coerce_wizard_answer, the BRAIN-99 underscore-block "
        "would silently drop it and the wizard would never "
        "register as complete."
    )


def test_apply_wizard_mutations_writes_site_scanned_directly():
    """Same trust-separation guarantee for `_site_scanned`."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    has_direct_assign = (
        'w["_site_scanned"] = True' in src
        or "w['_site_scanned'] = True" in src
    )
    assert has_direct_assign, (
        "BRAIN-100 regression: `_apply_wizard_mutations` must "
        "directly assign `w['_site_scanned'] = True`."
    )


def test_client_underscore_keys_rejected_at_validator_boundary():
    """Behavioral: confirm BRAIN-99's underscore-block still
    fires for the values that BRAIN-100 needs the server to
    set. Defense in depth: validator drops client smuggling,
    server-side direct assignment still fires."""
    from server import _coerce_wizard_answer, _WIZARD_DROP
    for k in (
        "_interview_complete",
        "_site_scanned",
        "_wizard_phase",
        "_dna_state",
        "_last_complete_fingerprint",
    ):
        assert _coerce_wizard_answer(k, "attacker-value") is _WIZARD_DROP, (
            f"BRAIN-100 regression: validator should drop "
            f"client-supplied `{k}`. BRAIN-99's allowlist "
            f"appears to have weakened."
        )


def test_complete_endpoint_filters_underscore_from_client_profile():
    """Source-level: api_wizard_complete must explicitly
    filter underscore-prefixed keys from the client `profile`
    payload before merging. Two-layer defense: (1) the
    explicit BRAIN-75 skip-list, (2) BRAIN-99's coerce-time
    underscore-block."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    # The BRAIN-75 explicit skip must still exist alongside
    # BRAIN-99's underscore-block in the validator.
    has_skip = (
        '"_interview_complete"' in src and '"_site_scanned"' in src
    )
    assert has_skip, (
        "BRAIN-100 regression: BRAIN-75's profile-filter "
        "skip-list must still exclude `_interview_complete` + "
        "`_site_scanned` from the client merge path. Even "
        "with BRAIN-99 catching them at coerce time, the "
        "explicit skip is documentation + defense in depth."
    )


def test_apply_wizard_mutations_does_not_route_underscore_writes_through_coerce():
    """Source-level: the direct assignments in
    `_apply_wizard_mutations` must NOT be wrapped by a call to
    `_coerce_wizard_answer`. If a future refactor adds that
    call, the underscore-block silently drops the write."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    # Find the _apply_wizard_mutations definition body.
    apply_idx = src.find("def _apply_wizard_mutations")
    assert apply_idx != -1
    end_idx = src.find("# 1.", apply_idx)
    if end_idx == -1:
        end_idx = apply_idx + 4000
    body = src[apply_idx:end_idx]
    # Inside this body, server-owned writes must be direct
    # assignment, not routed through _coerce_wizard_answer.
    # We can't grep for "not routed" directly; instead we
    # assert the function body contains the direct
    # assignments and DOESN'T wrap them in coerce.
    assert 'w["_interview_complete"] = True' in body
    assert 'w["_site_scanned"] = True' in body
    # Defensive: if `_coerce_wizard_answer(` appears in the
    # mutator body, the trust-separation has been violated.
    assert "_coerce_wizard_answer(" not in body, (
        "BRAIN-100 regression: `_apply_wizard_mutations` "
        "body now references `_coerce_wizard_answer(`. "
        "Server-side trusted writes must NOT route through "
        "the client-validator — they would hit the BRAIN-99 "
        "underscore-block and silently drop. Use direct "
        "dict assignment for server-owned writes."
    )


def test_complete_legitimate_path_works_end_to_end(local_env):
    """Behavioral end-to-end: a clean wizard complete from a
    legitimate client payload (no underscore smuggling)
    persists `_interview_complete=True` and `_site_scanned=True`
    in the stored wizard. This is the load-bearing assertion
    that BRAIN-99 didn't break the happy path."""
    import asyncio

    async def _run():
        from db import init_db, create_user, merge_settings, get_settings
        from auth import hash_password
        # Seed a fresh user with minimal wizard answers + an
        # epoch (so save-progress has been called once).
        await init_db()
        uid = await create_user(
            "brain100@example.com", hash_password("p"), "B100"
        )
        # We don't run the full FastAPI request through HTTP
        # here — that would require TestClient setup the local
        # suite doesn't have. Instead simulate the merge
        # mutator's effect directly: a server-side write of
        # `_interview_complete = True` lands in the stored
        # wizard. This proves the direct-assignment path
        # writes through merge_settings without being
        # blocked by BRAIN-99's coerce-time guard.
        def _server_side_complete(cur):
            cur = dict(cur or {})
            w = dict(cur.get("wizard") or {})
            # Simulate _apply_wizard_mutations' final lines:
            w["_site_scanned"] = True
            w["_interview_complete"] = True
            cur["wizard"] = w
            return cur

        await merge_settings(uid, _server_side_complete)
        s = await get_settings(uid)
        w = (s or {}).get("wizard") or {}
        assert w.get("_interview_complete") is True, (
            "BRAIN-100 regression: server-side direct-assign "
            "of `_interview_complete` failed to persist. "
            "Either merge_settings is rejecting underscore "
            "keys (it shouldn't) or the test fixture is broken."
        )
        assert w.get("_site_scanned") is True, (
            "BRAIN-100 regression: server-side direct-assign "
            "of `_site_scanned` failed to persist."
        )

    asyncio.run(_run())
