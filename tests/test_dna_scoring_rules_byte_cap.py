"""Regression tests for BRAIN-134 (a503): DNA
`scoring_rules` is an AI-output string assembled by
`_dna_build_scoring_rules` from the Stage-1
`scoring_guide` payload. BRAIN-128 (a497) capped
phase-5 question text bytes; BRAIN-129 (a498) capped
scan-output field bytes. `scoring_rules` is the next
unbounded AI-output ingress into a persisted row.

Failure mode (Per Huntova engineering review on
LLM output handling + insecure-output guidance):

`generate_agent_dna` calls `_dna_build_scoring_rules`
which `\\n`.joins lines built from the Stage-1
`scoring_guide`:

```python
scoring = strategy.get("scoring_guide", {})
# ...
for sig in scoring.get("must_have_signals", []):
    lines.append(f"  - {sig}")
for sig in scoring.get("bonus_signals", []):
    lines.append(f"  + {sig}")
for sig in scoring.get("instant_reject", []):
    lines.append(f"  x {sig}")
lines.append(f"SCORE 10: {scoring.get('score_10', '')}")
# ... etc
```

A hallucinating provider can produce 1000-item
`must_have_signals` lists or a 50 KB `score_10`
band string. The result lands in `dna["scoring_rules"]`,
gets `json.dumps`'d into the `agent_dna.dna_json`
column, then re-loaded into `ctx._cached_dna` on
every agent loop start, and re-injected into every
prompt at app.py:3756-3757:

```python
if _dna.get("scoring_rules"):
    c += f"\\n═══ SCORING RULES ═══\\n{_dna['scoring_rules']}\\n"
```

Consequences:
- 100 KB+ `scoring_rules` bloats the agent_dna row.
- Every agent-loop start reloads the full string
  (no streaming).
- Every prompt to the AI provider eats the same
  budget repeatedly — direct user spend impact on
  BYOK keys.
- Slows BRAIN-86 canonicalization downstream when
  the DNA is re-derived.
- Defeats the point of caching — the bigger the
  string, the larger every cache hit.

Per Huntova engineering review + LLM-validation
guidance: prompt instructions alone do not reliably
control output length. Validated structured output
still needs field-level bounds matching storage and
prompt-budget limits.

Invariants:
- Module-scope constant `_DNA_FIELD_BYTES_MAX`
  (default 16 KiB, parity with
  `server._WIZARD_FIELD_BYTES_MAX`).
- `_dna_build_scoring_rules` clips its joined
  output to `_DNA_FIELD_BYTES_MAX` before returning.
- Output is round-trip valid UTF-8 (no orphan
  continuation bytes from a mid-codepoint truncation).
"""
from __future__ import annotations
import inspect


def test_dna_field_bytes_max_constant_exists():
    """Module-scope cap for DNA AI-output strings."""
    import app as _a
    val = getattr(_a, "_DNA_FIELD_BYTES_MAX", None)
    assert val is not None, (
        "BRAIN-134 regression: app must expose "
        "`_DNA_FIELD_BYTES_MAX`. AI-output DNA fields "
        "(scoring_rules, business_context, email_rules) "
        "need a per-field byte cap, parity with "
        "server._WIZARD_FIELD_BYTES_MAX."
    )
    assert isinstance(val, int) and val > 0
    # Sanity bounds: 4 KiB minimum (legitimate DNA
    # rules can be a few KB), 64 KiB max (anything
    # larger defeats the purpose).
    assert 4096 <= val <= 65536


def test_dna_build_scoring_rules_uses_clip_helper():
    """Source-level: `_dna_build_scoring_rules` must
    clip its joined output before returning. Without
    this, a hallucinating Stage-1 `scoring_guide` can
    produce an unbounded scoring_rules string."""
    from app import _dna_build_scoring_rules
    src = inspect.getsource(_dna_build_scoring_rules)
    assert "_DNA_FIELD_BYTES_MAX" in src or "_clip_dna_field" in src, (
        "BRAIN-134 regression: _dna_build_scoring_rules "
        "must reference `_DNA_FIELD_BYTES_MAX` (or a "
        "wrapping helper) to clip the joined output."
    )


def test_dna_build_scoring_rules_clamps_oversized_input():
    """Behavioral: a hallucinated `scoring_guide` with
    1000 must_have_signals or a 50 KB score_10 string
    must NOT produce a multi-MB scoring_rules
    output."""
    from app import _dna_build_scoring_rules
    import app as _a
    cap = _a._DNA_FIELD_BYTES_MAX
    # Worst-case AI hallucination: thousands of bullets
    # + giant score-band strings.
    huge_scoring = {
        "must_have_signals": [f"signal-{i} " + "x" * 200
                              for i in range(2000)],
        "bonus_signals": [f"bonus-{i} " + "y" * 200
                          for i in range(2000)],
        "instant_reject": [f"reject-{i} " + "z" * 200
                           for i in range(2000)],
        "score_10": "A" * 50_000,
        "score_7_8": "B" * 50_000,
        "score_4_6": "C" * 50_000,
        "score_1_3": "D" * 50_000,
        "score_0":   "E" * 50_000,
    }
    out = _dna_build_scoring_rules(huge_scoring)
    assert isinstance(out, str)
    assert len(out.encode("utf-8")) <= cap, (
        f"BRAIN-134 regression: scoring_rules was "
        f"{len(out.encode('utf-8'))} bytes, exceeds "
        f"cap {cap}."
    )


def test_dna_build_scoring_rules_round_trip_utf8_safe():
    """A truncation must not produce an orphan UTF-8
    continuation byte. Multi-byte chars at the
    boundary should round-trip cleanly."""
    from app import _dna_build_scoring_rules
    import app as _a
    cap = _a._DNA_FIELD_BYTES_MAX
    # Pad with multi-byte emoji so the truncation
    # boundary lands inside a 4-byte codepoint.
    huge_scoring = {
        "must_have_signals": ["🎯 " * (cap // 2)],
        "bonus_signals": [],
        "instant_reject": [],
        "score_10": "🎯" * (cap // 2),
        "score_7_8": "",
        "score_4_6": "",
        "score_1_3": "",
        "score_0": "",
    }
    out = _dna_build_scoring_rules(huge_scoring)
    # Must round-trip valid UTF-8.
    out.encode("utf-8").decode("utf-8")
    assert len(out.encode("utf-8")) <= cap


def test_dna_build_scoring_rules_preserves_short_input():
    """A normal-sized scoring_guide (the realistic
    case) must NOT be truncated. The cap is purely a
    hallucination guard."""
    from app import _dna_build_scoring_rules
    normal_scoring = {
        "must_have_signals": ["Has a website", "B2B sales"],
        "bonus_signals": ["Hiring sales reps"],
        "instant_reject": ["Job board"],
        "score_10": "Perfect ICP match",
        "score_7_8": "Strong fit",
        "score_4_6": "Maybe",
        "score_1_3": "Probably not",
        "score_0": "Reject",
    }
    out = _dna_build_scoring_rules(normal_scoring)
    assert "Has a website" in out
    assert "B2B sales" in out
    assert "Hiring sales reps" in out
    assert "Job board" in out
    assert "Perfect ICP match" in out
