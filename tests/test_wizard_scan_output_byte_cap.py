"""Regression tests for BRAIN-129 (a498): every
persisted long-text field on `_validate_scan_output`
must be clipped to a fixed byte budget at UTF-8
boundaries. BRAIN-127 capped user-input fields,
BRAIN-128 capped phase-5 AI-output. scan_report
(crawl + AI summarization output) is the third
ingress and was still unbounded at the byte level.

Failure mode (Per Huntova engineering review on
crawl + structured-output byte budgets):

`_SCAN_OUTPUT_SCHEMA` defines ~30 fields produced
by the crawl + AI summarization pipeline on
`/api/wizard/scan`. The validator
`_validate_scan_output` clips each string at
`_SCAN_STR_MAX = 50_000` chars — at up to 4 bytes
per UTF-8 char, that's ~200 KB per field. With
30 fields, the persisted scan output could
theoretically be 6 MB.

The body byte cap on the request (BRAIN-117) only
gates the URL submission. The crawl response is
constructed server-side from BeautifulSoup +
trafilatura output, so a verbose blog homepage or
a malformed extraction can produce one
disproportionately large field that survives into
the row. Then:

- BRAIN-86 canonicalization sorts + JSON-dumps the
  whole row including this oversized field on
  every fingerprint.
- BRAIN-85 fingerprint cache lookups hash a 6 MB
  payload.
- /api/wizard/status doesn't emit the scan output
  directly but it lives in the row, weighing every
  read.
- The phase-5 / complete pipelines feed parts of
  the scan output back into AI prompts (BRAIN-13
  clips per-field for prompt) — but the persisted
  field is the source of truth and stays bloated.

User input and phase-5 AI-output are now bounded.
The crawler's own persisted text is not. Per
Huntova engineering review on crawl + structured-
output byte budgets: every persisted long-text
field, including scan_report or equivalent crawl
text, must be clipped to a fixed byte budget at
UTF-8 boundaries before storage.

Invariants:
- Module-scope constant `_SCAN_FIELD_BYTES_MAX`
  (default 16 KiB, env-overridable).
- `_validate_scan_output` applies
  `_clip_to_byte_budget` to every `str` and each
  item in `list_str` fields, AFTER the existing
  char trim.
- The cap fires BEFORE merge into the persisted
  row.
"""
from __future__ import annotations
import inspect


def test_scan_field_bytes_max_constant_exists():
    """Module-scope constant defines the per-field
    byte cap for scan output."""
    import server as _s
    val = getattr(_s, "_SCAN_FIELD_BYTES_MAX", None)
    assert val is not None, (
        "BRAIN-129 regression: server must expose "
        "`_SCAN_FIELD_BYTES_MAX`. Crawl output is the "
        "third ingress (after user input + phase-5 AI) "
        "and needs the same byte-level guarantee."
    )
    assert isinstance(val, int) and val > 0
    # Sanity: real scan fields like business_description
    # are paragraphs (sub-1KB). 16 KiB is generous.
    # Floor at 4 KiB (legitimate paragraphs); ceiling at
    # 64 KiB (anything larger defeats the purpose).
    assert 4096 <= val <= 65536


def test_validate_scan_output_uses_byte_clip_helper():
    """Source-level: `_validate_scan_output` must call
    `_clip_to_byte_budget` somewhere — otherwise the
    50K char cap still permits 200 KB UTF-8 fields."""
    import server as _s
    src = inspect.getsource(_s._validate_scan_output)
    has_byte_cap = (
        "_clip_to_byte_budget(" in src
        or "_SCAN_FIELD_BYTES_MAX" in src
    )
    assert has_byte_cap, (
        "BRAIN-129 regression: `_validate_scan_output` "
        "must apply the per-field byte cap so a "
        "verbose crawl output can't bloat the row "
        "via a single oversized field."
    )


def test_validate_scan_output_clamps_giant_str_field():
    """Behavioral: a single 100 KB string field on a
    str-typed schema key must clamp to ≤ cap bytes."""
    import server as _s
    cap = _s._SCAN_FIELD_BYTES_MAX
    big = "Lorem ipsum dolor " * 10_000  # ~180 KB
    out = _s._validate_scan_output({
        "business_description": big,
    })
    assert "business_description" in out
    assert len(out["business_description"].encode("utf-8")) <= cap


def test_validate_scan_output_clamps_list_items():
    """Behavioral: each item in a list_str field
    enforces the byte cap."""
    import server as _s
    cap = _s._SCAN_FIELD_BYTES_MAX
    big_item = "x" * 100_000
    out = _s._validate_scan_output({
        "services": [big_item, "tiny", big_item],
    })
    assert "services" in out
    assert isinstance(out["services"], list)
    for item in out["services"]:
        assert len(item.encode("utf-8")) <= cap


def test_validate_scan_output_handles_multibyte():
    """Behavioral: multibyte UTF-8 in a scan field
    truncates at code-point boundary — output is
    valid UTF-8."""
    import server as _s
    cap = _s._SCAN_FIELD_BYTES_MAX
    text = "🌟" * (cap // 2)  # ~2× over the cap in bytes
    out = _s._validate_scan_output({"summary": text})
    assert "summary" in out
    encoded = out["summary"].encode("utf-8")
    decoded = encoded.decode("utf-8")
    assert decoded == out["summary"]
    assert len(encoded) <= cap


def test_validate_scan_output_normal_payload_unchanged():
    """Sanity: a normal-sized scan response passes
    through untouched."""
    import server as _s
    payload = {
        "company_name": "Acme Co",
        "business_description": "We help SMBs scale.",
        "services": ["consulting", "training"],
        "outreach_tone": "consultative",
    }
    out = _s._validate_scan_output(payload)
    assert out["company_name"] == "Acme Co"
    assert out["business_description"] == "We help SMBs scale."
    assert out["services"] == ["consulting", "training"]
    assert out["outreach_tone"] == "consultative"


def test_validate_scan_output_list_str_tolerant_path_clamps():
    """Behavioral: the tolerant str-for-list_str
    coercion path (single string → [string]) also
    enforces the byte cap."""
    import server as _s
    cap = _s._SCAN_FIELD_BYTES_MAX
    big = "y" * 100_000
    out = _s._validate_scan_output({"services": big})
    assert "services" in out
    assert isinstance(out["services"], list)
    assert len(out["services"]) == 1
    assert len(out["services"][0].encode("utf-8")) <= cap
