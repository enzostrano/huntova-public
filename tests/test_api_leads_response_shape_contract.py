"""Regression tests for BRAIN-PROD-3 (a508): the
`/api/leads` response-shape contract between
server.py and the two readers in templates/jarvis.html.

Failure mode (Per Huntova engineering review on
client/server contract drift):

`/api/leads` is implemented at server.py and ends
with `return leads` — a bare Python list, which
FastAPI serialises as a JSON array. Both readers
in `templates/jarvis.html` (main `loadLeads()` +
lead-detail drill-in refetch) extracted leads with
`(d && d.leads) || []`. On a JSON array `d.leads`
is `undefined`; the fallback to `[]` fired every
time and `_leads` was always empty.

User-visible symptom: the sidebar pill (sourced
from `/api/status` → `db.get_leads_count` SQL
COUNT(*)) reported "Leads 1" while the main Leads
view rendered "No leads yet" — a contradiction
that masked real lead deliveries until the user
dug into the SSE feed.

Invariants this test guards:

1. The `/api/leads` handler still returns a list-
   shape (or a wrap that contains it under
   `.leads`). The loose form so a future server-
   side wrap doesn't break the test, just the
   frontend reader if it doesn't ALSO update.
2. Both readers in `templates/jarvis.html` do
   shape coercion — accept array OR `{leads:[…]}`.
3. The broken `(d && d.leads) || []` pattern is
   GONE from the template. Without this guard a
   refactor could re-introduce the unwrap and the
   bug silently returns.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATE = _REPO_ROOT / "templates" / "jarvis.html"


def _template_text() -> str:
    return _TEMPLATE.read_text(encoding="utf-8")


def test_api_leads_handler_emits_list_or_wrapped_list():
    """Source-level: the `/api/leads` handler must
    return a value that the frontend reader can
    consume — either the bare list (current contract)
    or a `{leads: [...]}` wrap. The test accepts both
    so a future server-side wrap doesn't break this
    guard, only the readers if they don't ALSO update.
    """
    from server import api_leads
    src = inspect.getsource(api_leads)
    # Locate the final return statement of the handler.
    # We look for either `return leads` (bare list) OR
    # `return {"leads": leads}` / `return {"leads":` (wrap).
    has_bare_return = bool(re.search(r"return\s+leads\b", src))
    has_wrapped_return = bool(re.search(r'return\s*\{\s*["\']leads["\']\s*:', src))
    assert has_bare_return or has_wrapped_return, (
        "BRAIN-PROD-3 regression: /api/leads must return "
        "either a bare `leads` list or a `{leads: [...]}` "
        "wrap. Anything else breaks the frontend reader's "
        "shape coercion contract."
    )


def test_jarvis_leads_readers_use_shape_coercion():
    """Source-level: both `/api/leads` readers in
    templates/jarvis.html must coerce array OR
    `{leads:[…]}` shapes. Anchor on the
    `Array.isArray(d)` ternary that the a508 fix
    introduced — that's the canonical shape-coercion
    pattern. We expect at least two occurrences (one
    per reader call site).
    """
    src = _template_text()
    # Find all instances of the canonical coercion.
    matches = re.findall(
        r"Array\.isArray\(d\)\s*\?\s*d\s*:\s*"
        r"\(\s*\(\s*d\s*&&\s*Array\.isArray\(d\.leads\)\s*\)"
        r"\s*\?\s*d\.leads\s*:\s*\[\s*\]\s*\)",
        src,
    )
    assert len(matches) >= 2, (
        "BRAIN-PROD-3 regression: both /api/leads readers "
        "in templates/jarvis.html (loadLeads + lead-detail "
        "drill-in) must use the canonical "
        "`Array.isArray(d) ? d : ((d && Array.isArray(d.leads)) ? d.leads : [])` "
        "shape coercion. Found %d matches; expected ≥2." % len(matches)
    )


def test_jarvis_leads_readers_drop_broken_unwrap():
    """Source-level: the broken pattern
    `((d && d.leads) || []).slice()` (or the same
    without the .slice()) must be GONE from
    templates/jarvis.html in any /api/leads context.
    Without this guard a refactor could re-introduce
    the unwrap and the bug silently returns — the
    frontend would once again render "No leads yet"
    for users who actually have leads.
    """
    src = _template_text()
    # The exact broken pattern that a508 replaced.
    broken_one = "((d && d.leads) || []).slice()"
    assert broken_one not in src, (
        "BRAIN-PROD-3 regression: the broken unwrap "
        "`((d && d.leads) || []).slice()` is back in "
        "templates/jarvis.html. /api/leads returns a "
        "bare JSON array, so `d.leads` is `undefined` "
        "and `_leads` ends up `[]` — the Leads view "
        "renders 'No leads yet' even when the DB has "
        "rows and the sidebar pill reports a count."
    )
    # Defensive: also block the equivalent pattern
    # without `.slice()` in case a future contributor
    # inlines the assignment differently.
    broken_two = "(d && d.leads) || []"
    # We don't ban broken_two outright — the canonical
    # coercion contains the prefix `(d && Array.isArray(d.leads))`
    # which itself contains `(d && d` — so a substring
    # match would false-positive. Instead, ensure that
    # whenever the prefix appears, it's followed by
    # `Array.isArray(d.leads)` (the safe form) and not
    # by `d.leads) ||` (the broken form).
    for m in re.finditer(r"\(\s*d\s*&&\s*", src):
        # Look at the next 60 chars after the match.
        tail = src[m.end():m.end() + 60]
        if "d.leads) ||" in tail and "Array.isArray" not in tail:
            raise AssertionError(
                "BRAIN-PROD-3 regression: a `(d && d.leads) || …` "
                "unwrap of /api/leads is back in "
                "templates/jarvis.html. /api/leads returns a "
                "bare array; this pattern always falls back to "
                "the empty default."
            )


def test_jarvis_loadleads_uses_arr_local():
    """Source-level: the `loadLeads()` function uses
    a local `_arr` (or any locally-bound coerced array)
    before assigning to `_leads`. Anchored on the
    a508 fix idiom so a contributor can't silently
    revert to direct `_leads = (d && d.leads || []).slice()`.
    """
    src = _template_text()
    # The a508 fix uses `const _arr = Array.isArray(d) ? d : …`
    # then `_leads = _arr.slice()` — verify both halves
    # show up in the same neighbourhood at least twice
    # (loadLeads + lead-detail).
    arr_decl_count = len(re.findall(
        r"const\s+_arr\s*=\s*Array\.isArray\(d\)",
        src,
    ))
    assert arr_decl_count >= 2, (
        "BRAIN-PROD-3 regression: the a508 fix uses "
        "`const _arr = Array.isArray(d) ? d : …` in both "
        "/api/leads readers. Found %d occurrences; "
        "expected ≥2 (one per reader). A direct "
        "`_leads = (d && d.leads || []).slice()` is the "
        "broken pattern this test blocks." % arr_decl_count
    )
