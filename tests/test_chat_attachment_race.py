"""Regression tests for a610 — chat attachment race condition.

Failure mode (Per Huntova engineering review on chat surface
stability sweep):

`/api/chat` accepts an `attachments: [{id: <int>}]` list. The
frontend's `_uploadAttachment` ran in the background and only
populated `id` on success. If the user typed + hit Enter while
the upload was still in flight, the snapshot taken in `send()`
contained `{id: null}`. The dispatcher loop in server.py at
:4070 ran:

    _aid = int(_a.get("id") if isinstance(_a, dict) else _a)
    except (TypeError, ValueError):
        continue

`int(None)` raises TypeError → the per-attachment `try` swallows
it and `continue`s. Net effect: the user saw "(attached 1 image)"
in the chat feed, the AI got a text-only message, and replied as
if blind. Zero indication of the silent drop to the user.

The fix has two layers:

1. Frontend (`templates/jarvis.html`):
   - `_uploadAttachment` stores its in-flight Promise on the
     placeholder. send() awaits all pending upload promises
     before snapshotting + filters out attachments without an id.
   - The chip shows "uploading…" + a pulsing border so it's
     visually obvious the upload is still in flight.
   - Body construction filters `id != null` belt-and-braces.

2. Server (`server.py`):
   - The dispatcher counts `_attachments_dropped` (claimed –
     kept) and reasons (`invalid_id`, `not_found`, `file_missing`,
     `oversize`, `read_error`).
   - Stamps `attachments_dropped` + `attachments_drop_reasons` +
     `attachments_claimed` + `attachments_kept` into `_chat_meta`
     so the dashboard can render a visible warning.

These tests guard against regression of the silent-drop behaviour
on either layer. They are static-analysis only (no FastAPI runner
needed) so they stay cheap and fast.
"""
from __future__ import annotations
import inspect
import re


def test_dispatcher_counts_attachments_claimed():
    """Server tracks the count of attachments the client claimed."""
    import server as _s
    src = inspect.getsource(_s.api_chat)
    assert "_att_claimed_count" in src, (
        "a610 regression: api_chat must track the count of "
        "client-claimed attachments separately from the count "
        "of attachments that actually reached the model. "
        "Without the claimed count we can't compute "
        "_attachments_dropped."
    )


def test_dispatcher_counts_attachments_dropped():
    """Server computes a drop count + surfaces it in chat meta."""
    import server as _s
    src = inspect.getsource(_s.api_chat)
    assert "_attachments_dropped" in src, (
        "a610 regression: api_chat must compute "
        "_attachments_dropped = claimed - kept so the frontend "
        "can render a visible warning when an image silently "
        "fails to attach."
    )
    assert "attachments_dropped" in src, (
        "a610 regression: the drop count must be stamped into "
        "the response meta envelope (key='attachments_dropped'). "
        "Without it the dashboard has no signal that an image "
        "didn't reach the model."
    )


def test_dispatcher_records_drop_reasons():
    """Each drop path records a machine-readable reason code."""
    import server as _s
    src = inspect.getsource(_s.api_chat)
    # The five drop reasons that have to map onto a continue
    # statement in the attachment loop.
    for reason in ("invalid_id", "not_found", "file_missing",
                    "oversize", "read_error"):
        assert reason in src, (
            f"a610 regression: dispatcher must record drop "
            f"reason '{reason}' so the dashboard can render a "
            f"specific failure hint. Found drops with no reason "
            f"would surface as 'unknown' to the user."
        )


def test_dispatcher_meta_carries_drop_envelope_when_nonzero():
    """When _attachments_dropped > 0 the meta block carries the
    full envelope (count, reasons, claimed, kept). The dashboard
    relies on all four fields to render a useful warning."""
    import server as _s
    src = inspect.getsource(_s.api_chat)
    # Assert the four keys are stamped in close proximity to
    # _attachments_dropped > 0 conditional. We can't easily AST
    # this, so substring is fine.
    for key in ("attachments_dropped", "attachments_drop_reasons",
                "attachments_claimed", "attachments_kept"):
        assert key in src, (
            f"a610 regression: meta envelope missing '{key}'. "
            f"All four keys are required so the warning renders "
            f"as 'N of M images didn't reach the model (reasons)'."
        )


def test_invalid_id_path_continues_not_raises():
    """The TypeError on int(None) must still be caught — the fix
    must not regress the previous silent-skip into a 500. The
    only change is that we now ALSO record the drop."""
    import server as _s
    src = inspect.getsource(_s.api_chat)
    # The except clause must still catch (TypeError, ValueError)
    # AND increment _att_drop_reasons before continuing.
    pat = re.compile(
        r"except\s*\(TypeError,\s*ValueError\)\s*:.*?"
        r"_att_drop_reasons\.append\([^)]*invalid_id[^)]*\).*?"
        r"continue",
        re.DOTALL,
    )
    assert pat.search(src), (
        "a610 regression: invalid id (int(None) TypeError) must "
        "be caught AND record drop reason 'invalid_id' AND "
        "continue. Pre-fix: caught + continued silently. "
        "Post-fix: caught + recorded + continued. Regression "
        "would either be (a) raising 500 or (b) losing the "
        "drop-reason record."
    )


def test_frontend_send_awaits_pending_uploads():
    """Frontend send() awaits every in-flight upload promise
    before snapshotting the attachment list."""
    src = open("templates/jarvis.html").read()
    # Look for the key invariant: send() detects uploading state
    # and awaits the upload promises.
    assert "uploading" in src and "uploadPromise" in src, (
        "a610 regression: _uploadAttachment must store the "
        "upload promise on the placeholder so send() can await "
        "it. Without uploadPromise, send() has no way to "
        "synchronise with the in-flight multipart upload."
    )
    # send() must call Promise.all on the stashed promises.
    assert "Promise.all" in src, (
        "a610 regression: send() must Promise.all the pending "
        "upload promises. A naive `if (uploading) return` would "
        "drop the user's send entirely; the right behaviour is "
        "to wait + then post."
    )


def test_frontend_filters_null_ids_in_body():
    """Body construction filters attachments without a real id.
    Belt-and-braces in case a future placeholder leaks past the
    promise gate."""
    src = open("templates/jarvis.html").read()
    # The body construction must filter `a.id != null` before mapping.
    pat = re.compile(
        r"\.filter\(\s*a\s*=>\s*a\s*&&\s*a\.id\s*!=\s*null\s*\)\s*"
        r"\.map\(",
        re.DOTALL,
    )
    assert pat.search(src), (
        "a610 regression: chat body construction must filter "
        "attachments with `id != null` before mapping. Without "
        "this, a synthetic placeholder pushed by a future code "
        "path would post `{id: null}` to /api/chat and trigger "
        "the silent-drop path on the server."
    )


def test_frontend_chip_surfaces_uploading_state():
    """The attach chip visibly shows uploading state — pre-fix
    the chip looked ready even while the multipart was still
    in flight, fooling the user into pressing Send too early."""
    src = open("templates/jarvis.html").read()
    assert "hv-attach-chip-loading" in src, (
        "a610 regression: chip must apply the loading class "
        "while uploading=true. Without a visual cue users can't "
        "tell whether their image is ready to send."
    )
    # The chip text must include 'uploading…' so it's labeled
    # not just a CSS pulse.
    assert "uploading…" in src or "uploading…" in src, (
        "a610 regression: chip must show the literal text "
        "'uploading…' next to the filename so the in-flight "
        "state is unambiguous, not just a subtle pulse."
    )


def test_frontend_warns_on_dropped_attachments():
    """When server stamps attachments_dropped > 0, frontend
    surfaces a warning row in the chat feed."""
    src = open("templates/jarvis.html").read()
    assert "attachments_dropped" in src, (
        "a610 regression: frontend must read meta."
        "attachments_dropped and render a visible warning. "
        "Pre-fix the silent-drop class produced ZERO user-"
        "visible signal — the AI replied as if blind."
    )
    # The warning must include "didn't reach the model" or
    # similar language so the user knows to re-attach.
    assert "didn't reach the model" in src or "didn’t reach the model" in src, (
        "a610 regression: dropped-attachment warning must say "
        "the image didn't reach the model. Pre-fix users had to "
        "guess from a blank reply why their image was ignored."
    )


def test_disp_keeps_idem_replay_path_intact():
    """The Idempotency-Key replay short-circuit must still fire
    BEFORE attachment processing — no point spending image-read
    + base64 cycles on a replay."""
    import server as _s
    src = inspect.getsource(_s.api_chat)
    idem_pos = src.find("_idempotency_lookup")
    att_pos = src.find("_attachment_payloads")
    assert 0 < idem_pos < att_pos, (
        "a610 regression: idempotency replay lookup must come "
        "before attachment loading. Loading + base64-encoding "
        "5 × 10 MB attachments on a known replay wastes I/O "
        "and ~50 MB of memory per duplicate POST."
    )
