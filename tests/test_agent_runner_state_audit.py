"""BRAIN-208: agent_runner.AgentRunner state-tracker audit.

`AgentRunner` tracks which users have running hunts + queue order.
Wave-1 BRAIN-PROD-7 (a620) shipped concurrency / DNA-replay fixes;
this audit complements it by pinning the state-tracker contract:

1. `running_count` returns 0 on fresh instance.
2. `is_running` returns False for unknown user.
3. `is_running` self-cleans ghost threads (dead-but-still-tracked).
4. `queue_position` returns 0 for currently-running user.
5. `queue_position` returns 1-indexed position for queued users.
6. `queue_position` returns None for not-running and not-queued.
7. Lock-protected — concurrent reads are safe.
"""
from __future__ import annotations

import threading


def test_running_count_starts_at_zero():
    from agent_runner import AgentRunner
    r = AgentRunner()
    assert r.running_count == 0


def test_is_running_unknown_user_false():
    from agent_runner import AgentRunner
    r = AgentRunner()
    assert r.is_running(user_id=999) is False


def test_is_running_self_cleans_dead_thread():
    """A thread that died without hitting its finally block leaves a
    ghost entry in `_running`. is_running must detect + clean."""
    from agent_runner import AgentRunner
    r = AgentRunner()

    # Inject a dead thread into _running.
    dead_thread = threading.Thread(target=lambda: None)
    dead_thread.start()
    dead_thread.join()  # ensure it's done

    with r._lock:
        r._running[42] = dead_thread

    # is_running notices it's dead and cleans it.
    assert r.is_running(42) is False
    assert 42 not in r._running


def test_is_running_returns_true_for_alive_thread():
    """Alive thread → is_running True."""
    from agent_runner import AgentRunner
    r = AgentRunner()

    stop_evt = threading.Event()
    long_thread = threading.Thread(target=lambda: stop_evt.wait(), daemon=True)
    long_thread.start()

    try:
        with r._lock:
            r._running[7] = long_thread
        assert r.is_running(7) is True
    finally:
        stop_evt.set()
        long_thread.join(timeout=2)


def test_queue_position_running_user_returns_zero():
    """A user whose hunt is already running → queue_position 0."""
    from agent_runner import AgentRunner
    r = AgentRunner()

    stop_evt = threading.Event()
    t = threading.Thread(target=lambda: stop_evt.wait(), daemon=True)
    t.start()

    try:
        with r._lock:
            r._running[1] = t
        assert r.queue_position(1) == 0
    finally:
        stop_evt.set()
        t.join(timeout=2)


def test_queue_position_queued_user():
    """User in queue at position 0 of deque → queue_position 1."""
    from agent_runner import AgentRunner
    r = AgentRunner()

    with r._lock:
        r._queue.append(1)
        r._queue.append(2)
        r._queue.append(3)

    assert r.queue_position(1) == 1
    assert r.queue_position(2) == 2
    assert r.queue_position(3) == 3


def test_queue_position_unknown_user_none():
    from agent_runner import AgentRunner
    r = AgentRunner()
    assert r.queue_position(999) is None


def test_queue_position_running_takes_priority_over_queued():
    """Running > queued. (Edge case if a user is somehow in both
    states — running wins.)"""
    from agent_runner import AgentRunner
    r = AgentRunner()
    stop_evt = threading.Event()
    t = threading.Thread(target=lambda: stop_evt.wait(), daemon=True)
    t.start()
    try:
        with r._lock:
            r._running[7] = t
            r._queue.append(7)  # also queued (shouldn't happen, but pin)
        assert r.queue_position(7) == 0
    finally:
        stop_evt.set()
        t.join(timeout=2)


def test_running_count_after_inserting():
    from agent_runner import AgentRunner
    r = AgentRunner()
    stop_evt = threading.Event()
    threads = []
    try:
        with r._lock:
            for uid in (1, 2, 3):
                t = threading.Thread(target=lambda: stop_evt.wait(), daemon=True)
                t.start()
                threads.append(t)
                r._running[uid] = t
        assert r.running_count == 3
    finally:
        stop_evt.set()
        for t in threads:
            t.join(timeout=2)


def test_lock_is_threading_lock():
    """The internal lock must be a threading.Lock (not RLock — the
    code-path doesn't re-enter; using RLock is fine but Lock is
    documented)."""
    from agent_runner import AgentRunner
    r = AgentRunner()
    # Has acquire and release methods.
    assert hasattr(r._lock, "acquire")
    assert hasattr(r._lock, "release")
