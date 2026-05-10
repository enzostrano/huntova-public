"""Tests for db.merge_settings — the atomic settings RW helper that a333
fixed (was silently broken on SQLite without _xlate). Verifies it
actually serialises concurrent writers + that the SQLite path works
end-to-end (regression for the bug that hid the helper for months).
"""
from __future__ import annotations

import asyncio


def test_merge_settings_basic(local_env):
    """Smoke test: merge_settings persists a value and a follow-up read sees it.

    Regression test for the a333 bug — `_merge_settings_sync` was
    crashing on every SQLite call with `near "%": syntax error`
    because the INSERT branch wasn't going through `_xlate()`.
    Without this test, the helper would silently break again on any
    future refactor that touched the SQL string.
    """
    async def _run():
        from db import merge_settings, get_settings, init_db, create_user
        from auth import hash_password
        await init_db()
        uid = await create_user("test@example.com", hash_password("p"), "T")
        await merge_settings(uid, lambda s: {"foo": "bar"})
        result = await get_settings(uid)
        assert result.get("foo") == "bar"
    asyncio.run(_run())


def test_merge_settings_serialises_concurrent_writers(local_env):
    """Two concurrent writers each write a different key; the final
    blob must contain BOTH keys. Regression for the lost-update
    pattern that a333/a334/a337/a345/a346 migrated away from.

    Without serialisation: writer A reads {}, writer B reads {},
    A writes {a:1}, B writes {b:2} — last-writer-wins → only one
    key in the final blob. With merge_settings's row lock: both
    keys land.
    """
    async def _run():
        from db import merge_settings, get_settings, init_db, create_user
        from auth import hash_password
        await init_db()
        uid = await create_user("race@example.com", hash_password("p"), "Race")

        async def _writer(key, value):
            def _mut(current):
                current = dict(current or {})
                # Busy loop widens the race window so a non-serialising
                # implementation reliably loses one of the writes.
                for _ in range(5000):
                    pass
                current[key] = value
                return current
            await merge_settings(uid, _mut)

        await asyncio.gather(_writer("alpha", "A"), _writer("beta", "B"))
        return await get_settings(uid)

    final = asyncio.run(_run())
    assert final.get("alpha") == "A", f"alpha key missing — final blob: {final}"
    assert final.get("beta") == "B", f"beta key missing — final blob: {final}"


def test_merge_settings_mutator_must_return_dict(local_env):
    """Non-dict return from the mutator must raise — defends against
    a callsite that forgets `return s` at the bottom of its mutator."""
    import pytest as _pytest
    async def _run():
        from db import merge_settings, init_db, create_user
        from auth import hash_password
        await init_db()
        uid = await create_user("badmut@example.com", hash_password("p"), "Bad")
        with _pytest.raises(ValueError, match="must return a dict"):
            await merge_settings(uid, lambda s: None)
        with _pytest.raises(ValueError, match="must return a dict"):
            await merge_settings(uid, lambda s: "not a dict")
    asyncio.run(_run())


# ── merge_lead tests (a348) ──
# Same shape as merge_settings — same silent-race bug existed in
# `_merge_lead_sync` until a348. Same regression coverage.


def test_merge_lead_basic(local_env):
    """Smoke test for merge_lead: persists a value and a follow-up
    read sees it. The UPDATE statement was raw-`%s` without _xlate
    pre-a348 — would crash on SQLite. This test would have caught it.
    """
    async def _run():
        from db import merge_lead, get_lead, upsert_lead, init_db, create_user
        from auth import hash_password
        await init_db()
        uid = await create_user("lead@example.com", hash_password("p"), "L")
        # Seed a lead row first — merge_lead needs an existing row.
        await upsert_lead(uid, "lead-1", {"org_name": "Acme", "fit_score": 7})
        result = await merge_lead(uid, "lead-1",
                                   lambda lead: {**lead, "email_status": "replied"})
        assert result is not None
        assert result.get("email_status") == "replied"
        # Verify it actually persisted.
        fetched = await get_lead(uid, "lead-1")
        assert fetched.get("email_status") == "replied"
    asyncio.run(_run())


def test_merge_lead_serialises_concurrent_writers(local_env):
    """Two concurrent writers each set a different field on the same
    lead; both must survive. Regression for the silent-race bug
    a348 fixed in `_merge_lead_sync`.
    """
    async def _run():
        from db import merge_lead, get_lead, upsert_lead, init_db, create_user
        from auth import hash_password
        await init_db()
        uid = await create_user("leadrace@example.com", hash_password("p"), "R")
        await upsert_lead(uid, "lead-r", {"org_name": "Acme"})

        async def _writer(field, value):
            def _mut(lead):
                lead = dict(lead or {})
                for _ in range(5000):
                    pass
                lead[field] = value
                return lead
            await merge_lead(uid, "lead-r", _mut)

        await asyncio.gather(_writer("email_subject", "Hello"),
                             _writer("contact_name", "Jane"))
        return await get_lead(uid, "lead-r")

    final = asyncio.run(_run())
    assert final.get("email_subject") == "Hello", f"email_subject lost — final: {final}"
    assert final.get("contact_name") == "Jane", f"contact_name lost — final: {final}"


def test_merge_lead_returns_none_for_missing_lead(local_env):
    """merge_lead on a non-existent lead_id should return None, not raise."""
    async def _run():
        from db import merge_lead, init_db, create_user
        from auth import hash_password
        await init_db()
        uid = await create_user("noslead@example.com", hash_password("p"), "N")
        result = await merge_lead(uid, "does-not-exist", lambda lead: lead)
        assert result is None
    asyncio.run(_run())
