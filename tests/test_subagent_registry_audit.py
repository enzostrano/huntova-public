"""BRAIN-191: agent_runner._SubagentRegistry invariant audit.

The subagent registry tracks per-user concurrent research / chat
spawn-fan-out workers. Pinned invariants:

1. `_Subagent.id` is 12-char hex, unique across instances.
2. `_Subagent` initial state is "starting".
3. `to_dict` returns the documented field set.
4. `register` enforces per-user concurrency cap.
5. `register` accepts up to N subagents within cap.
6. `cancel` flips status to "cancelled" for non-terminal entries
   (audit-wave-23 / a277 fix — frees the slot immediately).
7. `cancel` is a no-op for terminal-state subagents.
8. `cancel` rejects cross-user attempts (returns False).
9. `cancel` sets `cancel_event` so the runner thread observes it.
10. `list_user` returns dicts (not _Subagent instances directly).
11. `get` returns None for unknown id.
"""
from __future__ import annotations


def test_subagent_id_is_12_hex_chars():
    from agent_runner import _Subagent
    sa = _Subagent(user_id=1, kind="research", payload={})
    assert isinstance(sa.id, str)
    assert len(sa.id) == 12
    # Hex.
    int(sa.id, 16)


def test_subagent_id_unique():
    from agent_runner import _Subagent
    seen = {_Subagent(1, "x", {}).id for _ in range(50)}
    assert len(seen) == 50


def test_subagent_initial_state():
    from agent_runner import _Subagent
    sa = _Subagent(user_id=42, kind="research", payload={"q": "x"})
    assert sa.status == "starting"
    assert sa.user_id == 42
    assert sa.kind == "research"
    assert sa.payload == {"q": "x"}
    assert sa.started_at > 0
    assert sa.finished_at is None
    assert sa.result is None
    assert sa.error is None


def test_subagent_to_dict_fields():
    from agent_runner import _Subagent
    sa = _Subagent(user_id=1, kind="x", payload={})
    d = sa.to_dict()
    expected = {"id", "user_id", "kind", "payload", "status",
                "started_at", "finished_at", "result", "error"}
    assert set(d.keys()) == expected


def test_registry_get_unknown_returns_none():
    from agent_runner import _SubagentRegistry
    reg = _SubagentRegistry()
    assert reg.get("nonexistent-id") is None


def test_registry_list_empty_user():
    from agent_runner import _SubagentRegistry
    reg = _SubagentRegistry()
    assert reg.list_user(user_id=999) == []


def test_registry_register_returns_true_when_under_cap():
    from agent_runner import _SubagentRegistry, _Subagent
    reg = _SubagentRegistry()
    sa = _Subagent(user_id=1, kind="x", payload={})
    assert reg.register(sa) is True
    assert reg.get(sa.id) is sa


def test_registry_register_returns_false_when_at_cap(monkeypatch):
    """When cap is hit, register returns False."""
    from agent_runner import _SubagentRegistry, _Subagent
    import agent_runner
    # Force cap to 2 so we can hit it deterministically.
    monkeypatch.setattr(agent_runner, "_SUBAGENT_MAX_PER_USER", 2)
    # Stub load_settings to avoid disk I/O AND override cap.
    monkeypatch.setattr("app.load_settings", lambda: {})

    reg = _SubagentRegistry()
    a = _Subagent(user_id=1, kind="x", payload={})
    b = _Subagent(user_id=1, kind="x", payload={})
    c = _Subagent(user_id=1, kind="x", payload={})
    assert reg.register(a) is True
    assert reg.register(b) is True
    # Third — at cap.
    assert reg.register(c) is False


def test_cancel_flips_status_a277():
    """a277 fix: cancel of running subagent flips status to cancelled
    immediately, freeing the slot. Without this, register slot-count
    treated 'running' as occupied for ~10 min until cutoff sweep."""
    from agent_runner import _SubagentRegistry, _Subagent
    reg = _SubagentRegistry()
    sa = _Subagent(user_id=1, kind="x", payload={})
    reg.register(sa)
    sa.status = "running"
    assert reg.cancel(user_id=1, sub_id=sa.id) is True
    assert sa.status == "cancelled"
    assert sa.finished_at is not None
    assert sa.cancel_event.is_set()


def test_cancel_starting_subagent():
    from agent_runner import _SubagentRegistry, _Subagent
    reg = _SubagentRegistry()
    sa = _Subagent(user_id=1, kind="x", payload={})
    reg.register(sa)
    # Initial status = "starting".
    assert reg.cancel(user_id=1, sub_id=sa.id) is True
    assert sa.status == "cancelled"


def test_cancel_terminal_state_idempotent():
    """A subagent already 'done' / 'error' / 'cancelled' can be
    cancelled again as a no-op (returns True but doesn't change state)."""
    from agent_runner import _SubagentRegistry, _Subagent
    reg = _SubagentRegistry()
    sa = _Subagent(user_id=1, kind="x", payload={})
    reg.register(sa)
    sa.status = "done"
    sa.finished_at = 12345.0
    out = reg.cancel(user_id=1, sub_id=sa.id)
    assert out is True
    # Status preserved (already terminal).
    assert sa.status == "done"
    assert sa.finished_at == 12345.0


def test_cancel_cross_user_rejected():
    """User 2 cannot cancel user 1's subagent."""
    from agent_runner import _SubagentRegistry, _Subagent
    reg = _SubagentRegistry()
    sa = _Subagent(user_id=1, kind="x", payload={})
    reg.register(sa)
    out = reg.cancel(user_id=2, sub_id=sa.id)
    assert out is False
    # Status unchanged.
    assert sa.status == "starting"


def test_cancel_unknown_id_returns_false():
    from agent_runner import _SubagentRegistry
    reg = _SubagentRegistry()
    out = reg.cancel(user_id=1, sub_id="nonexistent")
    assert out is False


def test_list_user_returns_dicts():
    from agent_runner import _SubagentRegistry, _Subagent
    reg = _SubagentRegistry()
    sa = _Subagent(user_id=1, kind="x", payload={})
    reg.register(sa)
    out = reg.list_user(user_id=1)
    assert len(out) == 1
    # Each entry is a dict, not the _Subagent instance.
    assert isinstance(out[0], dict)
    assert out[0]["id"] == sa.id


def test_cancel_event_propagates():
    """The cancel_event flag must be set so the runner thread can
    observe it via cancel_event.is_set() poll."""
    from agent_runner import _Subagent
    sa = _Subagent(user_id=1, kind="x", payload={})
    assert sa.cancel_event.is_set() is False
    sa.cancel_event.set()
    assert sa.cancel_event.is_set() is True
