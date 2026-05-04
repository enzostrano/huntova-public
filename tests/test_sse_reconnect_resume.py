"""Regression tests for BRAIN-158 (a630): SSE reconnect-resume via
Last-Event-ID + per-user replay ring buffer + gap-detection marker.

Failure mode the fix addresses:

The browser EventSource auto-resends the `Last-Event-ID` header on
every reconnect (after a transient drop, OS-level pause when the tab
backgrounds, proxy idle-timeout, etc.). Before this fix:

1. `UserEventBus.emit()` produced SSE frames with NO `id:` line, so
   the browser never had a `Last-Event-ID` to send back.
2. `/agent/events` ignored the header even if a manual client sent
   one (e.g. a non-EventSource reconnect after a long pause).
3. There was no per-user history of recent events, so even with the
   header there was nothing to replay.

Net result: every reconnect was a fresh feed. Every event emitted
during the disconnect window — including newly-scored leads, status
changes, and progress snapshots — was permanently lost. The 5s/8s
`/api/status` polling fallback closes most state gaps but does NOT
deliver missed lead/log/thought events.

Invariants asserted here:

- Every emit() carries a monotonic `id: N\\n` line.
- Replay returns nothing for None / unparseable / current cursor.
- Replay returns the full tail when the cursor is older than newest.
- A cursor older than the buffer's oldest entry triggers a `_gap`
  marker event whose payload tells the client to refetch state.
- The gap marker itself is assigned a fresh id so the client's
  Last-Event-ID advances past the gap and won't re-trigger it.
- Screenshot events are exempt from the replay buffer (too large).
- Log events are exempt (too noisy — would evict load-bearing
  status/lead/progress frames).
- Replay buffer caps at `_REPLAY_BUFFER_MAX` and evicts oldest first.
- Truncation marker (BRAIN-147) is itself replayable — clients see
  the same `_truncated` JSON they would have seen live.
"""
from __future__ import annotations

import json
import re

import pytest

from user_context import UserEventBus


def _frame_id(frame: str) -> int:
    m = re.match(r"id: (\d+)\n", frame)
    assert m is not None, f"frame missing id: line: {frame!r}"
    return int(m.group(1))


def _frame_event(frame: str) -> str:
    m = re.search(r"\nevent: (\S+)\n", "\n" + frame)
    assert m is not None, f"frame missing event: line: {frame!r}"
    return m.group(1)


def _frame_data(frame: str) -> dict:
    m = re.search(r"\ndata: (.*?)\n\n", frame, re.DOTALL)
    assert m is not None, f"frame missing data: line: {frame!r}"
    return json.loads(m.group(1))


def test_emit_assigns_monotonic_event_ids():
    """Every SSE frame must carry an `id: N` line so the browser can
    store it as Last-Event-ID. IDs must be strictly increasing."""
    bus = UserEventBus()
    q = bus.subscribe()
    # Drain any cached frames pushed at subscribe-time.
    while not q.empty():
        q.get_nowait()
    bus.emit("status", {"text": "running", "state": "running"})
    bus.emit("lead", {"id": "abc", "url": "https://example.com"})
    bus.emit("progress", {"checked": 5})
    f1 = q.get_nowait()
    f2 = q.get_nowait()
    f3 = q.get_nowait()
    id1, id2, id3 = _frame_id(f1), _frame_id(f2), _frame_id(f3)
    assert id1 < id2 < id3
    # IDs are contiguous within a single bus instance.
    assert id2 == id1 + 1
    assert id3 == id2 + 1


def test_replay_since_returns_missed_frames():
    """A reconnect with Last-Event-ID = N replays every event emitted
    AFTER N — that's the whole point of resume."""
    bus = UserEventBus()
    bus.emit("status", {"text": "started", "state": "running"})  # id 1
    bus.emit("lead", {"id": "lead-1"})  # id 2
    cursor_after_first_two = 2
    # Simulate the disconnect window — three events the client missed.
    bus.emit("lead", {"id": "lead-2"})  # id 3
    bus.emit("progress", {"checked": 3})  # id 4
    bus.emit("status", {"text": "still running", "state": "running"})  # id 5

    replayed = bus.replay_since(cursor_after_first_two)
    assert len(replayed) == 3
    assert [_frame_id(f) for f in replayed] == [3, 4, 5]
    assert _frame_event(replayed[0]) == "lead"
    assert _frame_data(replayed[0])["id"] == "lead-2"


def test_replay_since_handles_missing_or_invalid_cursor():
    """No Last-Event-ID, garbage value, or a cursor at/past newest →
    nothing to replay (don't ship spurious history)."""
    bus = UserEventBus()
    bus.emit("status", {"text": "started", "state": "running"})  # id 1
    bus.emit("lead", {"id": "x"})  # id 2

    assert bus.replay_since(None) == []
    assert bus.replay_since("") == []
    assert bus.replay_since("not-a-number") == []
    # Cursor at current newest → caller is already up to date.
    assert bus.replay_since(2) == []
    # Cursor past current newest → also nothing.
    assert bus.replay_since(99) == []


def test_replay_emits_gap_marker_when_cursor_older_than_buffer():
    """If the disconnect lasted long enough that the events the
    client missed have been evicted from the ring buffer, the bus
    emits a `_gap` marker with `advice: refetch_full_state` so the
    client knows to re-pull /api/status. Without this, the client
    would silently desync."""
    bus = UserEventBus()
    # Force a tiny buffer so we can overflow it deterministically.
    bus._replay_buffer.maxlen  # sanity check the deque has a maxlen
    # Emit one event so the buffer's oldest_id is 1, then evict it
    # by emitting REPLAY_BUFFER_MAX more.
    bus.emit("status", {"text": "first", "state": "running"})  # id 1
    cursor_from_long_ago = 1
    for i in range(bus._REPLAY_BUFFER_MAX + 5):
        bus.emit("progress", {"checked": i})

    replayed = bus.replay_since(cursor_from_long_ago)
    assert len(replayed) > 0
    gap = replayed[0]
    assert _frame_event(gap) == "_gap"
    payload = _frame_data(gap)
    assert payload["advice"] == "refetch_full_state"
    assert payload["missed_from"] == 1
    # The gap marker itself has a fresh id so a subsequent reconnect
    # with that id won't re-trigger it.
    gap_id = _frame_id(gap)
    assert gap_id > 1


def test_screenshot_and_log_events_skip_replay_buffer():
    """Screenshots are huge (JPEG b64) and log lines are firehose-y —
    keeping either in the replay buffer would crowd out the
    load-bearing status/lead/progress frames the UI actually needs
    to recover from a disconnect."""
    bus = UserEventBus()
    bus.emit("screenshot", {"img": "x" * 100, "url": "https://example.com"})
    bus.emit("log", {"msg": "fetching url", "level": "info"})
    bus.emit("lead", {"id": "real-lead"})
    # replay from id 0 should return only the lead, not the screenshot
    # or the log.
    replayed = bus.replay_since(0)
    events = [_frame_event(f) for f in replayed]
    assert "screenshot" not in events
    assert "log" not in events
    assert "lead" in events


def test_replay_buffer_evicts_oldest_first_under_pressure():
    """The deque maxlen guarantees old entries get pushed out when
    the buffer is full. Replay still returns valid frames (no
    KeyError, no negative-id math) and the oldest_id reported in the
    gap marker matches what's actually in the buffer."""
    bus = UserEventBus()
    cap = bus._REPLAY_BUFFER_MAX
    # Emit cap + 50 events → first 50 should be evicted.
    for i in range(cap + 50):
        bus.emit("progress", {"checked": i})
    # Buffer should now hold exactly `cap` entries.
    assert len(bus._replay_buffer) == cap
    oldest_id, _ = bus._replay_buffer[0]
    newest_id, _ = bus._replay_buffer[-1]
    assert newest_id - oldest_id == cap - 1
    # Replay from cursor=0 → gap marker + buffered frames.
    replayed = bus.replay_since(0)
    assert _frame_event(replayed[0]) == "_gap"
    assert _frame_data(replayed[0])["buffered_from"] == oldest_id
    assert _frame_data(replayed[0])["buffered_to"] == newest_id


def test_truncation_marker_replays_with_correct_event_type():
    """BRAIN-147 clips oversized payloads to a `_truncated: true`
    marker. The truncated frame is what live subscribers receive,
    so it must also be what reconnecting clients receive on replay
    — otherwise the client's view of the run history would diverge
    based on whether they were connected at emit-time."""
    bus = UserEventBus()
    huge = {"id": "lead-huge", "page_text": "x" * (64 * 1024)}
    bus.emit("lead", huge)
    replayed = bus.replay_since(0)
    assert len(replayed) == 1
    assert _frame_event(replayed[0]) == "lead"
    payload = _frame_data(replayed[0])
    assert payload.get("_truncated") is True
    assert payload.get("reason") == "event_oversize"
    # Identifying fields preserved through truncation AND through
    # replay.
    assert payload.get("id") == "lead-huge"
