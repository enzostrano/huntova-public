"""Regression tests for BRAIN-127 (a496): every user-
writable long-text wizard field must enforce its own
maximum encoded byte length BEFORE merge / canonicalization.
Top-level body cap doesn't catch the "few keys, one
massive field" hole.

Failure mode (Per Huntova engineering review on
field-level byte caps + OWASP API4:2023 unrestricted
resource consumption):

BRAIN-117/118 (a486/a487) introduced the top-level
`_WIZARD_BODY_BYTES_MAX = 256 KiB` cap on every
wizard mutating route. BRAIN-13 (a374) clips
individual fields to 400-4000 chars BEFORE feeding
into AI prompts. `_WIZARD_STR_MAX = 50_000` clips
each persisted-row field to 50K chars.

The gap: 50K chars × 4 bytes/char (UTF-8 max) =
200 KB per field. A client can send a body well
under 256 KiB total but with a single 200 KB field
that:
- Survives the body cap.
- Bloats the persisted SQLite row.
- Weighs down BRAIN-86 canonicalization (key sort
  + JSON dump on every fingerprint).
- Slows BRAIN-85 fingerprint cache lookups.
- Inflates every subsequent get-mutate-save cycle
  on the row.

Real wizard fields are paragraphs — `outreach_tone`
is a sentence, `business_description` is a paragraph
(<1 KB). 16 KiB per field is 10× the longest
legitimate answer; tight enough that pathological
single-field inputs are rejected before merge.

Per Huntova engineering review on field-level byte
caps: every user-writable long-text wizard field
must have its own maximum encoded byte length
enforced before merge or canonicalization.

Invariants:
- Module-scope constant `_WIZARD_FIELD_BYTES_MAX`
  (default 16384 = 16 KiB, env-overridable).
- Helper `_clip_to_byte_budget(text, max_bytes)`
  truncates safely at a UTF-8 code-point boundary.
- `_coerce_wizard_answer` applies the byte cap to
  every string field AND to each item in list_str
  fields, AFTER the existing char trim.
- The cap fires BEFORE merge_settings — corrupted
  field-level inputs never reach the row.
"""
from __future__ import annotations
import inspect


def test_field_bytes_max_constant_exists():
    """Module-scope constant defines the per-field
    byte cap."""
    import server as _s
    val = getattr(_s, "_WIZARD_FIELD_BYTES_MAX", None)
    assert val is not None, (
        "BRAIN-127 regression: server must expose "
        "`_WIZARD_FIELD_BYTES_MAX`. Top-level body cap "
        "(256 KiB) doesn't catch the few-keys-one-massive-"
        "field hole."
    )
    assert isinstance(val, int) and val > 0
    # Sanity bounds: real wizard fields are sub-1KB; 16
    # KiB is generous. 4 KiB minimum (BRAIN-13 prompt
    # budget); 64 KiB max (anything larger defeats the
    # purpose).
    assert 4096 <= val <= 65536


def test_clip_to_byte_budget_helper_exists():
    """Module-scope helper does the actual clip."""
    import server as _s
    fn = getattr(_s, "_clip_to_byte_budget", None)
    assert fn is not None and callable(fn), (
        "BRAIN-127 regression: server must expose "
        "`_clip_to_byte_budget(text, max_bytes)`."
    )


def test_clip_to_byte_budget_passes_under_cap():
    """Behavioral: a string under the cap returns
    unchanged."""
    import server as _s
    text = "warm and direct"
    out = _s._clip_to_byte_budget(text, 1024)
    assert out == text


def test_clip_to_byte_budget_truncates_oversize_ascii():
    """Behavioral: ASCII string over the cap truncates
    to the cap."""
    import server as _s
    text = "x" * 100_000  # 100K chars = 100K bytes ASCII
    out = _s._clip_to_byte_budget(text, 1024)
    assert len(out.encode("utf-8")) <= 1024


def test_clip_to_byte_budget_handles_multibyte_safely():
    """Behavioral: a multibyte UTF-8 string truncated
    at an arbitrary byte index must not produce an
    invalid UTF-8 sequence (no orphan continuation
    bytes). The helper must round down to the nearest
    code-point boundary."""
    import server as _s
    # "🌟" is 4 bytes in UTF-8. 100 of them = 400 bytes.
    text = "🌟" * 100
    out = _s._clip_to_byte_budget(text, 50)
    # The output must be valid UTF-8 — encode/decode
    # round-trip must succeed without errors.
    encoded = out.encode("utf-8")
    decoded = encoded.decode("utf-8")
    assert decoded == out
    # And it must respect the byte cap.
    assert len(encoded) <= 50


def test_clip_to_byte_budget_handles_empty_and_none():
    """Defensive: empty strings and None pass through
    cleanly."""
    import server as _s
    assert _s._clip_to_byte_budget("", 100) == ""
    # None should return "" or pass through; either is
    # acceptable as long as it doesn't raise.
    out = _s._clip_to_byte_budget(None, 100)
    assert out in (None, "")


def test_coerce_wizard_answer_applies_byte_cap():
    """Source-level: `_coerce_wizard_answer` must call
    the byte clipper somewhere — otherwise a single
    100 KB field survives into merge_settings."""
    import server as _s
    src = inspect.getsource(_s._coerce_wizard_answer)
    has_byte_cap = (
        "_clip_to_byte_budget(" in src
        or "_WIZARD_FIELD_BYTES_MAX" in src
    )
    assert has_byte_cap, (
        "BRAIN-127 regression: `_coerce_wizard_answer` "
        "must apply the per-field byte cap so a single "
        "oversized field can't survive into the "
        "persisted row."
    )


def test_coerce_wizard_answer_clamps_giant_string_field():
    """Behavioral: a single 100 KB string field
    coerced via `_coerce_wizard_answer` for a known
    schema key (e.g. `business_description`) must
    return a value whose UTF-8 byte size is ≤ the
    cap."""
    import server as _s
    cap = _s._WIZARD_FIELD_BYTES_MAX
    giant = "Lorem ipsum dolor " * 10_000  # ~180 KB
    out = _s._coerce_wizard_answer("business_description", giant)
    assert isinstance(out, str)
    assert len(out.encode("utf-8")) <= cap, (
        "BRAIN-127 regression: a single oversized field "
        "must clamp to <= _WIZARD_FIELD_BYTES_MAX."
    )


def test_coerce_wizard_answer_clamps_list_items():
    """Behavioral: each item in a list_str field also
    enforces the byte cap."""
    import server as _s
    cap = _s._WIZARD_FIELD_BYTES_MAX
    big_item = "x" * 100_000
    out = _s._coerce_wizard_answer("regions", [big_item, "tiny"])
    assert isinstance(out, list)
    for item in out:
        assert isinstance(item, str)
        assert len(item.encode("utf-8")) <= cap


def test_complete_history_clips_via_byte_cap():
    """Source-level: api_wizard_complete's history
    coercion (line ~9382-83) must also use the byte
    cap so a hostile history payload can't sneak past
    via the parallel path."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    # Find the history clip section.
    idx = src.find("for _h in raw_history")
    if idx == -1:
        idx = src.find("raw_history")
    assert idx >= 0, "history clip section should exist"
    block = src[idx:idx + 2500]
    has_byte_cap = (
        "_clip_to_byte_budget(" in block
        or "_WIZARD_FIELD_BYTES_MAX" in block
    )
    assert has_byte_cap, (
        "BRAIN-127 regression: api_wizard_complete's "
        "history clip must apply `_clip_to_byte_budget` "
        "(or reference `_WIZARD_FIELD_BYTES_MAX`) so a "
        "history payload with a 100 KB question or "
        "answer string can't survive into the persisted "
        "row via the parallel path."
    )
