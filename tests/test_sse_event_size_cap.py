"""Regression tests for BRAIN-147 (a560): every SSE frame
emitted via `UserEventBus.emit` must fit under
`_SSE_EVENT_BYTES_MAX` so a single oversized lead /
log / status payload cannot break the frontend
EventSource parser for the rest of the run.

Failure mode (Per Huntova engineering review on
SSE-frame size hygiene):

The agent thread emits SSE events via
`ctx.bus.emit(event_type, data)`. Real `lead` payloads
are ~2-5 KiB. But a buggy or adversarial code path can
produce a much larger one — e.g. a `lead` dict that
accidentally carries the full scraped page text, a `log`
line built from a runaway provider error repr, or a
`thought` that includes the raw AI response. The browser
EventSource splits on `\n\n` and parses one frame at a
time; a 50 KiB+ frame can stall or break parsing for the
rest of the stream depending on browser + network.

The new cap (32 KiB) is generous for legitimate payloads
and tight enough that any frame above it is a bug we
want to clip rather than ship.

Invariants:
- `_SSE_EVENT_BYTES_MAX` exists as a class constant.
- A small payload round-trips unchanged (no false-
  positive truncation).
- An oversized payload gets replaced with a truncation
  marker (`{"_truncated": true, "reason":
  "event_oversize", "type": <event>}`).
- The marker frame's JSON is itself well under the
  cap, so the SSE stream stays parseable.
- Lightweight identifying fields (lead_id, run_id,
  state) survive truncation so the frontend can still
  correlate the dropped frame.
- The `screenshot` event type is exempt — JPEG
  screenshots are intentionally large.
- A non-serialisable payload (e.g. raw object) doesn't
  crash emit() — it gets replaced with a safe marker.
- The actual SSE frame written to subscribers respects
  the cap end-to-end (not just the helper).
"""
from __future__ import annotations
import json
import queue


def test_sse_event_bytes_max_constant_exists():
    """Constant must exist on UserEventBus."""
    from user_context import UserEventBus
    assert hasattr(UserEventBus, "_SSE_EVENT_BYTES_MAX"), (
        "BRAIN-147 regression: UserEventBus must expose "
        "`_SSE_EVENT_BYTES_MAX` so operators can audit "
        "the per-frame cap from a single source."
    )
    cap = UserEventBus._SSE_EVENT_BYTES_MAX
    assert isinstance(cap, int), "cap must be an int (bytes)"
    assert 8 * 1024 <= cap <= 256 * 1024, (
        f"cap should be a sensible KiB value; got {cap}"
    )


def test_clip_helper_exists():
    """The clipping helper must be addressable for callers /
    test code that wants to apply the same logic upstream."""
    from user_context import UserEventBus
    assert hasattr(UserEventBus, "_clip_sse_event_payload"), (
        "BRAIN-147 regression: the clipping helper must be a "
        "named method, not inlined into emit() — keeps the "
        "policy auditable in one place."
    )


def test_small_payload_passes_through_unchanged():
    """A tiny lead must serialise as plain JSON, not be
    spuriously replaced with the truncation marker."""
    from user_context import UserEventBus
    data = {"id": 1, "company": "Acme", "score": 8.5}
    out = UserEventBus._clip_sse_event_payload("lead", data)
    parsed = json.loads(out)
    assert parsed == data, (
        "small payloads must round-trip exactly — false-"
        "positive truncation breaks the live feed."
    )
    assert "_truncated" not in parsed


def test_oversized_payload_is_clipped():
    """A 100 KiB lead must be replaced with the marker
    so the SSE frame stays small."""
    from user_context import UserEventBus
    huge_blob = "x" * (100 * 1024)
    data = {"id": 42, "company": "Acme", "page_text": huge_blob}
    out = UserEventBus._clip_sse_event_payload("lead", data)
    parsed = json.loads(out)
    assert parsed.get("_truncated") is True, (
        "BRAIN-147 regression: oversized payloads must "
        "carry the `_truncated: true` marker so the "
        "frontend knows the frame was clipped."
    )
    assert parsed.get("reason") == "event_oversize"
    assert parsed.get("type") == "lead"
    # Marker is itself well under the cap.
    assert len(out.encode("utf-8")) < UserEventBus._SSE_EVENT_BYTES_MAX


def test_truncation_marker_preserves_lead_id():
    """The frontend correlates research progress / dnf
    events by lead_id — the marker must keep that field
    when present so the dropped frame can still be
    routed to the right card."""
    from user_context import UserEventBus
    huge_blob = "y" * (50 * 1024)
    data = {"lead_id": 1234, "step": huge_blob}
    out = UserEventBus._clip_sse_event_payload(
        "research_progress", data
    )
    parsed = json.loads(out)
    assert parsed.get("_truncated") is True
    assert parsed.get("lead_id") == 1234, (
        "BRAIN-147 regression: lead_id must survive "
        "truncation — the frontend uses it to route "
        "research_progress frames."
    )


def test_screenshot_event_is_exempt():
    """JPEG screenshots are intentionally large (q=55,
    b64-encoded). They must NOT be clipped by the cap —
    the screenshot pipeline already controls its own
    size budget via JPEG quality."""
    from user_context import UserEventBus
    big_b64 = "A" * (60 * 1024)
    data = {"img": big_b64, "url": "https://example.com", "ts": "12:00:00"}
    out = UserEventBus._clip_sse_event_payload("screenshot", data)
    parsed = json.loads(out)
    assert "_truncated" not in parsed, (
        "BRAIN-147 invariant: `screenshot` is exempt — "
        "intentionally large by design. Clipping it would "
        "break the live screenshot pane."
    )
    assert parsed["img"] == big_b64


def test_unserialisable_payload_doesnt_crash():
    """A payload containing a raw non-serialisable object
    must not crash emit() — must fall back to a marker."""
    from user_context import UserEventBus

    class _Weird:
        pass

    # Note: json.dumps(default=str) handles most weird objects,
    # but a circular reference still raises ValueError. Use that
    # as the worst-case test.
    circular = {}
    circular["self"] = circular
    out = UserEventBus._clip_sse_event_payload("log", circular)
    parsed = json.loads(out)
    assert parsed.get("_truncated") is True
    assert parsed.get("reason") in (
        "event_unserialisable", "event_oversize",
    )


def test_emit_writes_clipped_frame_to_subscriber():
    """End-to-end: a real subscriber must receive a small,
    clipped SSE frame — not the original 100 KiB payload.
    Without this, the helper could be present but never
    actually wired into emit()."""
    from user_context import UserEventBus
    bus = UserEventBus()
    q = bus.subscribe()
    try:
        huge = {"id": 7, "blob": "z" * (80 * 1024)}
        bus.emit("lead", huge)
        # Drain any cached replay frames first; the lead frame is the
        # one we just emitted.
        msg = None
        while True:
            try:
                m = q.get_nowait()
            except queue.Empty:
                break
            if "event: lead" in m:
                msg = m
                break
        assert msg is not None, "lead frame must have been delivered"
        # Frame must be small and contain the marker.
        assert len(msg.encode("utf-8")) < UserEventBus._SSE_EVENT_BYTES_MAX + 256, (
            "BRAIN-147 regression: emit() must apply the "
            "byte cap end-to-end. A 80 KiB payload reached "
            "the subscriber unclipped — the live feed will "
            "stall on the first oversized frame."
        )
        assert "_truncated" in msg
        assert "event_oversize" in msg
    finally:
        bus.unsubscribe(q)


def test_emit_passes_normal_lead_unchanged():
    """A realistic lead (~2 KiB) must reach the subscriber
    with all its fields intact."""
    from user_context import UserEventBus
    bus = UserEventBus()
    q = bus.subscribe()
    try:
        lead = {
            "id": 1,
            "company": "Acme Corp",
            "domain": "acme.example",
            "score": 8.2,
            "fit": 8, "buyability": 7, "reachability": 6,
            "email": "ops@acme.example",
            "summary": "Mid-size SaaS with a procurement gap our agent fits.",
            "tech": ["postgres", "react"],
            "country": "US",
        }
        bus.emit("lead", lead)
        msg = None
        while True:
            try:
                m = q.get_nowait()
            except queue.Empty:
                break
            if "event: lead" in m:
                msg = m
                break
        assert msg is not None
        # Extract the data: line and parse — must equal original lead.
        for line in msg.splitlines():
            if line.startswith("data: "):
                parsed = json.loads(line[len("data: "):])
                break
        else:
            raise AssertionError("no data line in SSE frame")
        assert parsed == lead, (
            "BRAIN-147 invariant: realistic-size leads must "
            "round-trip without modification."
        )
    finally:
        bus.unsubscribe(q)
