"""Regression test for API-1: /api/track-actions must validate
that `actions` items are dicts before calling `.get()`.

Failure mode (per untrusted-JSON-shape audit pattern, BRAIN-73
class):

`api_track_actions` accepts a JSON body with `{"actions": [...]}`
from sendBeacon-style telemetry. Pre-fix, the implementation
only checked `isinstance(actions, list)` and then iterated
`actions[:50]` calling `a.get("lead_id", "")` and
`a.get("action", "")` on each item.

If the client posts:

    {"actions": ["foo", "bar"]}        # list of strings
    {"actions": [1, 2, 3]}              # list of ints
    {"actions": [null, null]}           # list of nulls
    {"actions": [["nested", "list"]]}   # list of lists

the handler crashed with `AttributeError: 'str' object has no
attribute 'get'` and returned 500 with full stack trace.
sendBeacon is fire-and-forget so the user saw no error, but
the server emitted 500 in logs on every navigation.

Fix: `isinstance(a, dict)` per item before `.get()`. Mirrors
the BRAIN-73 / -74 / -75 / -76 wizard-payload-shape pattern.
"""
from __future__ import annotations
import inspect


def test_track_actions_validates_item_shape():
    """Source-level: the per-action loop must guard `.get()` calls
    with an isinstance(dict) check, OR coerce non-dict items
    cleanly. Pre-fix the loop crashed on the first non-dict item."""
    from server import api_track_actions
    src = inspect.getsource(api_track_actions)
    has_guard = (
        "isinstance(a, dict)" in src
        or "isinstance(_a, dict)" in src
        or "isinstance(item, dict)" in src
        or "_validate_action_item" in src
    )
    assert has_guard, (
        "API-1 regression: api_track_actions iterates `actions[:50]` "
        "and calls `a.get(...)` without verifying each item is a "
        "dict. A list of strings/ints/None crashes with "
        "AttributeError 500. Add `if not isinstance(a, dict): "
        "continue` at the top of the loop."
    )


def test_track_actions_does_not_crash_on_non_dict_items():
    """End-to-end: the handler must complete with 200 (or 4xx) — never
    bubble an AttributeError 500 — when actions contains scalars."""
    import asyncio
    import json as _json
    from unittest.mock import AsyncMock, MagicMock

    from server import api_track_actions

    fake_request = MagicMock()
    fake_request.json = AsyncMock(return_value={"actions": ["foo", 42, None, ["x"]]})
    fake_user = {"id": 1, "email": "test@example.com", "tier": "free"}

    try:
        result = asyncio.run(api_track_actions(fake_request, fake_user))
    except AttributeError as e:
        raise AssertionError(
            f"API-1 regression: api_track_actions crashed with "
            f"AttributeError on a list-of-non-dict actions payload: "
            f"{e}. Skip non-dict items at the top of the loop."
        ) from e
    if hasattr(result, "body"):
        body_str = result.body.decode() if isinstance(result.body, bytes) else str(result.body)
        body = _json.loads(body_str)
    else:
        body = result
    assert body.get("saved", 0) == 0 or body.get("ok") is True, (
        f"API-1 regression: handler returned unexpected shape "
        f"on non-dict items: {body!r}"
    )
