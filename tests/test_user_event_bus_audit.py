"""BRAIN-190: user_context.UserEventBus invariant audit.

The per-user SSE bus is the only path between the agent thread and
the frontend EventSource. Pinned invariants:

1. `subscribe()` returns a fresh queue with cached terminal /
   running-status / progress events pre-loaded.
2. `unsubscribe()` removes the queue from the subscriber set.
3. `emit_keepalive()` sends a SSE comment (begins with `:`) to all
   subscribers; doesn't trigger event listeners on the frontend.
4. `_clip_sse_event_payload` (BRAIN-147): payloads > 32 KiB are
   replaced with a small truncation marker preserving lead_id /
   run_id when present.
5. Screenshot events bypass the byte cap (intentionally large).
6. Non-serialisable payloads fall through to the marker (no raise).
7. `_TERMINAL_STATES` set has the expected entries.
8. `_DEAD_THRESHOLD` < `_MAXSIZE` (audit wave bug #11 — drop dead
   clients early).
"""
from __future__ import annotations

import json


def test_subscribe_returns_queue():
    from user_context import UserEventBus
    bus = UserEventBus()
    q = bus.subscribe()
    import queue as _q
    assert isinstance(q, _q.Queue)


def test_unsubscribe_removes_queue():
    from user_context import UserEventBus
    bus = UserEventBus()
    q = bus.subscribe()
    assert q in bus._subscribers
    bus.unsubscribe(q)
    assert q not in bus._subscribers


def test_subscribe_replays_terminal_state():
    """A new subscriber after a terminal event should see it cached."""
    from user_context import UserEventBus
    bus = UserEventBus()
    bus._last_terminal = "data: terminal_event\n\n"
    q = bus.subscribe()
    # Queue should have the terminal state pre-loaded.
    assert not q.empty()
    item = q.get_nowait()
    assert "terminal" in item


def test_subscribe_replays_progress():
    from user_context import UserEventBus
    bus = UserEventBus()
    bus._last_progress = "data: progress_event\n\n"
    q = bus.subscribe()
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    assert any("progress" in i for i in items)


def test_emit_keepalive_sends_comment_line():
    from user_context import UserEventBus
    bus = UserEventBus()
    q = bus.subscribe()
    # Drain initial replay if any.
    while not q.empty():
        q.get_nowait()

    bus.emit_keepalive()
    item = q.get_nowait()
    # SSE comment lines begin with `:` and are ignored by EventSource.
    assert item.startswith(":")
    assert "keepalive" in item


def test_keepalive_drops_full_subscriber():
    """A subscriber whose queue is full when keepalive fires gets
    discarded — dead-client cleanup."""
    from user_context import UserEventBus
    import queue as _q
    bus = UserEventBus()
    # Override maxsize for testability.
    full_q = _q.Queue(maxsize=1)
    full_q.put("placeholder")  # now full
    bus._subscribers.add(full_q)
    bus.emit_keepalive()
    # Full queue should have been discarded.
    assert full_q not in bus._subscribers


def test_clip_sse_event_under_cap_returns_serialised():
    from user_context import UserEventBus
    small = {"lead_id": "abc", "score": 8}
    out = UserEventBus._clip_sse_event_payload("lead", small)
    assert "abc" in out
    assert "8" in out
    # Validates as JSON.
    parsed = json.loads(out)
    assert parsed["lead_id"] == "abc"


def test_clip_sse_event_over_cap_truncated():
    """A 100KB payload triggers the truncation marker."""
    from user_context import UserEventBus
    huge = {"lead_id": "the_lead", "huge_blob": "x" * 100_000}
    out = UserEventBus._clip_sse_event_payload("lead", huge)
    parsed = json.loads(out)
    assert parsed.get("_truncated") is True
    assert parsed["reason"] == "event_oversize"
    assert parsed["type"] == "lead"
    # Identifying keys preserved.
    assert parsed.get("lead_id") == "the_lead"


def test_clip_sse_event_screenshot_bypasses_cap():
    """Screenshot events are intentionally large — must NOT be clipped."""
    from user_context import UserEventBus
    huge = {"image_b64": "A" * 100_000}
    out = UserEventBus._clip_sse_event_payload("screenshot", huge)
    parsed = json.loads(out)
    # Should NOT have truncation marker.
    assert "_truncated" not in parsed
    # Original data preserved.
    assert "image_b64" in parsed


def test_clip_sse_event_unserialisable_does_not_raise():
    """Even a payload that json.dumps can't serialise normally won't
    raise — `default=str` rescues most cases, and a try/except wraps
    truly broken cases in the truncation marker."""
    from user_context import UserEventBus

    class Unserialisable:
        def __init__(self):
            pass

    out = UserEventBus._clip_sse_event_payload("lead", Unserialisable())
    # Must return a string (no raise).
    assert isinstance(out, str)
    # Must be valid JSON (parseable).
    parsed = json.loads(out)
    # Either rescued via default=str (a string) or marker dict.
    assert parsed is not None


def test_clip_sse_event_preserves_run_id():
    """When lead_id absent but run_id present, marker still preserves it."""
    from user_context import UserEventBus
    huge = {"run_id": 42, "data": "x" * 100_000}
    out = UserEventBus._clip_sse_event_payload("progress", huge)
    parsed = json.loads(out)
    assert parsed.get("_truncated") is True
    assert parsed.get("run_id") == 42


def test_terminal_states_constant():
    from user_context import UserEventBus
    expected = {"idle", "stopped", "error", "exhausted", "completed"}
    assert UserEventBus._TERMINAL_STATES == expected


def test_dead_threshold_below_maxsize():
    """Multi-agent bug #11 fix: dead-threshold drops before fillup."""
    from user_context import UserEventBus
    assert UserEventBus._DEAD_THRESHOLD < UserEventBus._MAXSIZE


def test_sse_event_bytes_max_constant():
    """BRAIN-147 byte cap: 32 KiB."""
    from user_context import UserEventBus
    assert UserEventBus._SSE_EVENT_BYTES_MAX == 32 * 1024


def test_sse_oversize_exempt_events():
    """Screenshot is exempt — pin that constant."""
    from user_context import UserEventBus
    assert "screenshot" in UserEventBus._SSE_OVERSIZE_EXEMPT_EVENTS


def test_clip_sse_event_marker_fits_within_cap():
    """The truncation marker itself must fit inside the byte cap
    (otherwise replacing oversized with marker is pointless)."""
    from user_context import UserEventBus
    huge = {"x": "y" * 200_000, "lead_id": "abc"}
    out = UserEventBus._clip_sse_event_payload("lead", huge)
    assert len(out.encode("utf-8")) < UserEventBus._SSE_EVENT_BYTES_MAX
