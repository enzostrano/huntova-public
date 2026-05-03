"""Regression tests for BRAIN-74 (a435): /api/wizard/scan AI
summarization output must be validated against a closed schema
before being returned to the client.

Failure mode (per GPT-5.4 untrusted-LLM-output audit):

`api_wizard_scan` (`server.py:7578+`) called
`_parse_ai_json(raw)` and returned the parsed dict directly to
the client. The AI prompt asks for ~30 fields with specific types
(strings, lists of strings, enums like `price_tier:
budget|midrange|premium|enterprise`), but pre-fix nothing
validated:

- AI returns `services: {"evil": "nested-dict"}` instead of a
  list → flows to client → client prefills wizard answers from
  it → if user clicks Continue without editing, BRAIN-73 catches
  it on the way back. But the client's `_brainState.scanData`
  also gets passed to `/api/wizard/generate-phase5` as
  `scanData`, and that endpoint formats fields into prompt text
  via `_clip_for_prompt`. A dict-shaped `services` becomes
  `services: {'evil': 'nested-dict'}` in the prompt — the AI
  follow-up generator sees garbage.
- AI returns `price_tier: "free-text-with-prompt-injection"`
  bypassing the budget|midrange|premium|enterprise enum.
- AI returns `summary` with a 200KB string of repeated tokens
  (model failure mode).
- AI returns `__proto__` or `_internal_secret` keys (prompt
  injection attempt).
- AI returns mixed-type list elements in `services`,
  `industries_served`, `buying_triggers`, `value_propositions`
  → downstream prompt assembly does `", ".join(str(x) ...)`
  which silently coerces but loses signal + leaks dict
  representations.

The standard defense: validate AI structured output at the
server boundary BEFORE returning to the client. Same pattern as
BRAIN-73 for `_wizard_answers`, but specific to scan output
(different field set + enum constraints).

Invariants:
- `_SCAN_OUTPUT_SCHEMA` declares allowed fields + types + (where
  applicable) enum constraints.
- `_validate_scan_output(analysis)` runs before the JSONResponse.
- Unknown keys dropped (closed schema).
- Wrong-type values rejected/coerced.
- Enum values reset to default if outside the allowed set.
- Strings capped at a per-scan-field max.
- Server-set fields (`_site_text`, `_url`, `_crawl_method`,
  `_pages_seen`) bypass the schema since they're trusted.
"""
from __future__ import annotations
import inspect


def test_scan_endpoint_validates_ai_output_against_schema():
    """Source-level: api_wizard_scan must run analysis through a
    validation function before returning. Either a named helper
    `_validate_scan_output` or a referenced `_SCAN_OUTPUT_SCHEMA`
    must appear in the endpoint."""
    from server import api_wizard_scan
    src = inspect.getsource(api_wizard_scan)
    has_validator = (
        "_validate_scan_output" in src
        or "_coerce_scan_output" in src
        or "_SCAN_OUTPUT_SCHEMA" in src
    )
    assert has_validator, (
        "BRAIN-74 regression: scan endpoint must validate AI "
        "output against a closed schema before returning to the "
        "client. Pre-fix, malformed structured output flowed "
        "straight through, polluting downstream prompt assembly "
        "and wizard prefill."
    )


def test_scan_validator_drops_unknown_keys():
    """Unknown keys (e.g. prompt-injection attempts like
    `_internal_secret`, `__proto__`, or AI hallucinated extras)
    must be dropped. Schema is closed."""
    import server as _s
    # The validator must exist as a callable.
    validator = getattr(_s, "_validate_scan_output", None) or \
                getattr(_s, "_coerce_scan_output", None)
    assert validator is not None, (
        "BRAIN-74 regression: scan validator function must be "
        "defined and importable."
    )
    raw = {
        "company_name": "Acme",
        "summary": "ok summary",
        "_internal_admin": "smuggled",
        "__proto__": {"polluted": True},
        "made_up_field": [1, 2, 3],
    }
    out = validator(raw)
    assert "company_name" in out
    for bad in ("_internal_admin", "__proto__", "made_up_field"):
        assert bad not in out, (
            f"BRAIN-74 regression: unknown key '{bad}' survived "
            "scan validation. Schema is closed."
        )


def test_scan_validator_rejects_dict_for_list_fields():
    """List-of-string fields (services, industries_served,
    buying_triggers, value_propositions, pain_points_addressed,
    differentiators, etc.) must reject dict shapes. The AI
    sometimes emits `{"0": "first item", "1": "second"}` style
    when the JSON-mode fails to coerce — that's a malformed
    output, not a legitimate transformation."""
    import server as _s
    validator = getattr(_s, "_validate_scan_output", None) or \
                getattr(_s, "_coerce_scan_output", None)
    raw = {
        "services": {"evil": "nested"},
        "industries_served": ["DTC skincare", {"bad": "item"}, 42, "Series A SaaS"],
    }
    out = validator(raw)
    # services: dict → either dropped or empty list. NEVER a dict.
    services = out.get("services")
    assert services is None or isinstance(services, list), (
        f"BRAIN-74 regression: dict for services persisted as "
        f"{type(services).__name__}={services!r}. Reject."
    )
    # industries_served: list-of-mixed → filtered to strings only.
    inds = out.get("industries_served") or []
    for item in inds:
        assert isinstance(item, str), (
            f"BRAIN-74 regression: industries_served leaked "
            f"non-string {type(item).__name__}={item!r}."
        )


def test_scan_validator_enforces_enum_fields():
    """Fields with enum constraints (`price_tier`, `company_size`,
    `delivery_method`, `revenue_model`, `outreach_tone`) must
    only accept values from their declared enum. AI sometimes
    invents a new value (e.g. "free-text-with-injection") or
    silently corrupts the field — drop or default in those
    cases, never persist out-of-enum."""
    import server as _s
    validator = getattr(_s, "_validate_scan_output", None) or \
                getattr(_s, "_coerce_scan_output", None)
    raw = {
        "price_tier": "free-text-prompt-injection-attempt",
        "company_size": "GIGANTIC",  # not in {solo|small|medium|large}
        "delivery_method": "telepathic",
    }
    out = validator(raw)
    # Each enum field is either dropped (preferred) or coerced to
    # a known default. Never the malicious input.
    pt = out.get("price_tier")
    assert pt in (None, "", "budget", "midrange", "premium", "enterprise"), (
        f"BRAIN-74 regression: invalid price_tier '{pt}' persisted."
    )
    cs = out.get("company_size")
    assert cs in (None, "", "solo", "small", "medium", "large"), (
        f"BRAIN-74 regression: invalid company_size '{cs}' persisted."
    )
    dm = out.get("delivery_method")
    assert dm in (None, "", "remote", "onsite", "hybrid", "digital_product"), (
        f"BRAIN-74 regression: invalid delivery_method '{dm}' persisted."
    )


def test_scan_validator_caps_oversized_strings():
    """A 200KB string in any text field must not flow through.
    The scan response gets stored in `_site_text` (already capped
    at 6000 chars server-side) but other fields had no cap. AI
    failure modes can produce repeated-token bombs that bloat
    the response payload + downstream prompt assembly."""
    import server as _s
    validator = getattr(_s, "_validate_scan_output", None) or \
                getattr(_s, "_coerce_scan_output", None)
    huge = "X" * 200_000
    raw = {"summary": huge, "business_description": huge}
    out = validator(raw)
    for field in ("summary", "business_description"):
        v = out.get(field) or ""
        assert isinstance(v, str)
        assert len(v) < 100_000, (
            f"BRAIN-74 regression: {field} persisted at "
            f"{len(v)} chars — must be capped."
        )


def test_scan_validator_preserves_legitimate_payload():
    """Don't regress: a well-formed AI output must flow through
    unchanged (modulo whitespace trimming)."""
    import server as _s
    validator = getattr(_s, "_validate_scan_output", None) or \
                getattr(_s, "_coerce_scan_output", None)
    good = {
        "company_name": "Acme Corp",
        "summary": "We do X for Y.",
        "business_description": "We help DTC skincare brands scale past 1M MRR.",
        "services": ["Shopify migration", "CRO retainer"],
        "industries_served": ["DTC skincare", "Series A B2B SaaS"],
        "price_tier": "midrange",
        "company_size": "small",
        "delivery_method": "remote",
        "regions": ["United States", "United Kingdom"],
        "buyer_roles": ["Founder", "Marketing Director"],
        "buying_triggers": ["when their existing Shopify stops scaling"],
    }
    out = validator(good)
    assert out.get("company_name") == "Acme Corp"
    assert out.get("price_tier") == "midrange"
    assert isinstance(out.get("services"), list) and "Shopify migration" in out["services"]
    assert isinstance(out.get("regions"), list) and "United States" in out["regions"]
