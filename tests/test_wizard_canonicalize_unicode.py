"""Regression tests for BRAIN-137 (a512): the BRAIN-86 canonicalizer
must strip invisible-Unicode (BOM, zero-width, bidi marks) and
NFC-normalize so the BRAIN-85 idempotent fingerprint cache fires
on semantically-identical retries.

Failure mode (Per Huntova engineering review on idempotency-key
canonicalization, second-order):

BRAIN-86 (a455) added whitespace collapse + unordered-list
sorting + empty-vs-absent collapsing to the canonicalizer.
That handled the obvious drift modes, but two more invisible
classes still produced different fingerprints for semantically
identical content:

1. **Invisible Unicode**: BOM (U+FEFF), zero-width spaces
   (U+200B-U+200D), word joiner (U+2060), bidi direction
   marks (U+200E/U+200F/U+202A-U+202E/U+2066-U+2069), line
   and paragraph separators (U+2028/U+2029), and ASCII
   control characters all survive `str.split()`. A buggy
   client serializing `"Acme"` versus a BOM-prefixed
   `"\\ufeffAcme"` would hash to different fingerprints
   even though no human can see the difference.

2. **Unicode normalization form**: `"Cafe\\u0301"` (NFD —
   `e` + combining acute) versus `"Caf\\u00e9"` (NFC —
   precomposed `é`) render identically but hash to different
   bytes.

Each near-miss re-runs the entire BRAIN-72 brain build + DNA
generation pipeline. The user pays BYOK for the duplicate.

Invariants:
- `_normalize_invisible_unicode(s)` exists on `server` and
  strips invisible Unicode + applies NFC normalization.
- The canonicalizer's `_norm_string` calls it before
  whitespace collapse.
- Two payloads identical except for a leading BOM produce
  the same canonical form.
- NFC and NFD `é` produce the same canonical form.
- Zero-width spaces inside strings are stripped.
- Bidi direction marks (LRM, RLM, RLO, LRE, etc.) are
  stripped.
- Line / paragraph separators are stripped.
- ASCII control chars are stripped.
- Strip happens to list elements and history Q/A pairs too,
  not just top-level scalars.
"""
from __future__ import annotations
import inspect
import hashlib
import json


def _fingerprint(profile, history):
    """Mirror what api_wizard_complete does (server.py ~line 9930)
    so the test asserts the actual hash collision, not just the
    intermediate canonical-form equality."""
    import server as _s
    canon_profile, canon_history = _s._canonicalize_complete_payload(
        profile, history
    )
    canonical = json.dumps(
        {"profile": canon_profile, "history": canon_history},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_normalize_invisible_unicode_helper_exists():
    """The helper must be a callable on `server` so the
    canonicalizer + future call-sites can share it."""
    import server as _s
    fn = getattr(_s, "_normalize_invisible_unicode", None)
    assert fn is not None and callable(fn), (
        "BRAIN-137 regression: server must expose "
        "`_normalize_invisible_unicode(s)` so the canonicalizer "
        "can strip BOM/zero-width/bidi marks before hashing."
    )


def test_canonicalizer_calls_invisible_unicode_helper():
    """Source-level: `_canonicalize_complete_payload` must call
    `_normalize_invisible_unicode` (or NFC-normalize directly).
    Otherwise the helper exists but is decoration."""
    import server as _s
    src = inspect.getsource(_s._canonicalize_complete_payload)
    assert (
        "_normalize_invisible_unicode" in src
        or "unicodedata.normalize" in src
    ), (
        "BRAIN-137 regression: _canonicalize_complete_payload must "
        "invoke the invisible-Unicode normalizer (or unicodedata "
        "directly) before whitespace-collapsing strings."
    )


def test_bom_drift_produces_same_fingerprint():
    """Leading BOM (U+FEFF) is invisible — payloads identical
    except for a BOM at byte zero must hash to the same
    fingerprint or BRAIN-85 misses the duplicate."""
    a = _fingerprint({"company_name": "Acme"}, [])
    b = _fingerprint({"company_name": "﻿Acme"}, [])
    c = _fingerprint({"company_name": "Acme﻿"}, [])
    assert a == b == c, (
        f"BRAIN-137 regression: BOM-prefixed/suffixed strings must "
        f"hash identically. a={a} b={b} c={c}"
    )


def test_nfc_nfd_drift_produces_same_fingerprint():
    """NFD `Cafe + COMBINING ACUTE` and NFC `Café` look identical
    on screen and must hash identically. Otherwise an iOS keyboard
    that emits NFD vs an Android one that emits NFC will defeat
    the cache for a user with accented characters in their
    company name / region / etc."""
    nfc = "Café"           # é precomposed
    nfd = "Café"          # e + combining acute
    assert nfc != nfd, "test setup: NFC and NFD must differ at the byte level"
    a = _fingerprint({"company_name": nfc}, [])
    b = _fingerprint({"company_name": nfd}, [])
    assert a == b, (
        f"BRAIN-137 regression: NFC and NFD forms of `Café` must "
        f"hash identically after canonicalization. a={a} b={b}"
    )


def test_zero_width_space_inside_string_stripped():
    """Zero-width space (U+200B) inside a string must be stripped
    — `Pa<ZWSP>ris` and `Paris` are visually identical."""
    a = _fingerprint({"target_clients": "Paris"}, [])
    b = _fingerprint({"target_clients": "Pa​ris"}, [])
    c = _fingerprint({"target_clients": "P​a​r​i​s"}, [])
    assert a == b == c, (
        f"BRAIN-137 regression: zero-width spaces inside strings "
        f"must be stripped. a={a} b={b} c={c}"
    )


def test_zero_width_joiner_and_non_joiner_stripped():
    """ZWJ (U+200D) and ZWNJ (U+200C) must also be stripped —
    they survive `str.split()` since they're not whitespace."""
    a = _fingerprint({"company_name": "Acme"}, [])
    b = _fingerprint({"company_name": "Ac‍me"}, [])
    c = _fingerprint({"company_name": "Ac‌me"}, [])
    assert a == b == c, (
        f"BRAIN-137 regression: ZWJ/ZWNJ must be stripped. "
        f"a={a} b={b} c={c}"
    )


def test_bidi_direction_marks_stripped():
    """LRM/RLM (U+200E/U+200F) and the bidi override family
    (U+202A-U+202E, U+2066-U+2069) must be stripped — common
    in copy-pastes from RTL contexts."""
    plain = _fingerprint({"company_name": "Acme"}, [])
    rlm = _fingerprint({"company_name": "Acme‏"}, [])
    lrm = _fingerprint({"company_name": "‎Acme"}, [])
    rlo = _fingerprint({"company_name": "‮Acme‬"}, [])
    fsi = _fingerprint({"company_name": "⁨Acme⁩"}, [])
    assert plain == rlm == lrm == rlo == fsi, (
        f"BRAIN-137 regression: bidi direction marks must be "
        f"stripped. plain={plain} rlm={rlm} lrm={lrm} rlo={rlo} "
        f"fsi={fsi}"
    )


def test_line_and_paragraph_separators_stripped():
    """U+2028 (LINE SEPARATOR) and U+2029 (PARAGRAPH SEPARATOR)
    are not handled by `str.split()` and must be stripped — JSON
    parsers and JS clients handle these inconsistently."""
    a = _fingerprint({"company_name": "Acme"}, [])
    b = _fingerprint({"company_name": "Acme "}, [])
    c = _fingerprint({"company_name": "Ac me"}, [])
    assert a == b == c, (
        f"BRAIN-137 regression: line/paragraph separators must be "
        f"stripped. a={a} b={b} c={c}"
    )


def test_word_joiner_stripped():
    """U+2060 (WORD JOINER, the BOM replacement) must be
    stripped — common when text is pasted from Word."""
    a = _fingerprint({"company_name": "Acme"}, [])
    b = _fingerprint({"company_name": "Ac⁠me"}, [])
    assert a == b, (
        f"BRAIN-137 regression: word joiner must be stripped. "
        f"a={a} b={b}"
    )


def test_ascii_control_chars_stripped():
    """ASCII control chars (U+0001-U+001F minus \\t/\\n/\\r,
    plus U+007F DEL) must be stripped. Tab/newline/CR are left
    to whitespace collapse."""
    a = _fingerprint({"company_name": "Acme"}, [])
    b = _fingerprint({"company_name": "Acme\x01"}, [])
    c = _fingerprint({"company_name": "Ac\x07me"}, [])
    d = _fingerprint({"company_name": "Acme\x7f"}, [])
    assert a == b == c == d, (
        f"BRAIN-137 regression: ASCII control chars must be "
        f"stripped. a={a} b={b} c={c} d={d}"
    )


def test_invisible_unicode_inside_list_elements_stripped():
    """The strip must apply to list elements too, not just
    top-level scalars. `regions: ['United States']` and
    `regions: ['United States<BOM>']` represent the same set."""
    a = _fingerprint({"regions": ["United States", "Italy"]}, [])
    b = _fingerprint({"regions": ["﻿United States", "Italy"]}, [])
    c = _fingerprint({"regions": ["United States", "Ita​ly"]}, [])
    assert a == b == c, (
        f"BRAIN-137 regression: list elements must also be stripped. "
        f"a={a} b={b} c={c}"
    )


def test_invisible_unicode_inside_history_qa_stripped():
    """History Q/A pairs must also be stripped — that's where
    real user-pasted content lives. Note: zero-width spaces are
    stripped (not turned into spaces), so `B2B<ZWSP>SaaS` becomes
    `B2BSaaS` — semantically different from `B2B SaaS`. We exercise
    BOM + bidi marks here, which are unambiguously invisible."""
    a = _fingerprint(
        {},
        [{"question": "What is your ICP?", "answer": "B2B SaaS Series A"}],
    )
    b = _fingerprint(
        {},
        [{"question": "﻿What is your ICP?",
          "answer": "B2B SaaS Series A‏"}],
    )
    assert a == b, (
        f"BRAIN-137 regression: history Q/A strings must be "
        f"stripped. a={a} b={b}"
    )


def test_multiple_invisible_classes_combined():
    """Combination test — BOM + ZWSP + RLM + NFD all in the same
    payload must still hash to the plain ASCII version."""
    plain = _fingerprint(
        {"company_name": "Café Acme", "regions": ["United States"]},
        [{"question": "Q1", "answer": "A1"}],
    )
    poisoned = _fingerprint(
        {"company_name": "﻿Café​ Acme‏",
         "regions": ["‮United​ States"]},
        [{"question": "⁠Q1", "answer": "A1 "}],
    )
    assert plain == poisoned, (
        f"BRAIN-137 regression: combined invisible-Unicode "
        f"poisoning must canonicalize to the same fingerprint. "
        f"plain={plain} poisoned={poisoned}"
    )


def test_tab_newline_cr_still_collapse_as_whitespace():
    """\\t \\n \\r are intentionally NOT in the strip set — they
    must collapse via `str.split()` to a single space, same as
    BRAIN-86's existing behaviour."""
    a = _fingerprint({"company_name": "Acme Corp"}, [])
    b = _fingerprint({"company_name": "Acme\tCorp"}, [])
    c = _fingerprint({"company_name": "Acme\nCorp"}, [])
    d = _fingerprint({"company_name": "Acme\rCorp"}, [])
    assert a == b == c == d, (
        f"BRAIN-137 regression: tab/newline/CR must still collapse "
        f"to a single space. a={a} b={b} c={c} d={d}"
    )
