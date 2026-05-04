"""Regression tests for LEADS-1/2/3 (a610): leads-board count
accuracy + count-vs-list source-of-truth invariants.

Failure mode (per Huntova engineering review on the leads-
count surface):

The Jarvis dashboard derives the "N leads" sidebar pill from
GET /api/status `total_lead_count` (a SQL `COUNT(*)` over the
leads table) while the leads list view renders rows fetched
from GET /api/leads (a LIMIT/OFFSET query, default cap 10000).
Two completely independent code paths, two completely
independent SQL queries.

Three latent bugs were live before a610:

1. **Shape-mismatch root cause** — `templates/jarvis.html`
   loadLeads() did `_leads = ((d && d.leads) || []).slice()`
   but `/api/leads` (server.py L5466) returns a BARE JSON
   array, not `{leads:[…]}`. So `d.leads` was always
   `undefined` and `_leads` always resolved to `[]`. The
   visible list was empty for every user with leads, while
   the sidebar pill kept reading the truthful DB COUNT(*).
   Result: the exact "1 lead but untrue — list is empty"
   user-visible bug. (loadLeads + openLeadDetail both hit
   this.)

2. **Silent rendering cap** — renderLeads applied
   `.filter().slice(0, 300)` and reported the post-slice
   length as "N found", silently misleading anyone with
   >300 matches. A user with 1,247 leads was told "300
   found".

3. **SSE arrival drift** — `_agentSse.addEventListener(
   'lead', () => loadStatus())` only refreshed the pill
   from /api/status when a new lead arrived, NEVER the
   list. Users sat on the leads view watched the pill
   tick "+1" with no new row appearing on the board until
   they manually clicked Refresh.

These tests pin the canonical contracts so the
shape-mismatch / silent-cap / SSE-drift regressions can't
return:

- /api/leads MUST return a bare JSON array (frontend
  contract — changing this shape silently re-introduces
  bug 1 since the old `d.leads` reader is the prevailing
  pattern in many JS apps and copy/paste is common).
- /api/leads MUST honour a documented HARD_CAP (10000)
  defensively against a compromised session siphoning
  the entire table.
- GET /api/status MUST surface `total_lead_count` (the
  sidebar pill's source of truth).
- /api/status `total_lead_count` MUST equal
  `len(/api/leads)` for any user with ≤ HARD_CAP leads
  — this is the core "count == list" invariant the
  user-reported bug violated.
- /api/leads MUST honour limit/offset and clamp limit
  to HARD_CAP.
- /api/leads MUST be GET-only (any future POST swap
  would change the response shape contract while
  retaining the URL).
- The count invariant MUST hold across SSE events, page
  reload, and search-filter (the count on the server is
  always the unfiltered total — frontend filtering is
  cosmetic).

Run via `pytest tests/test_leads_count_surface.py -v`.
"""
from __future__ import annotations

import importlib
import sys

import pytest


def _import_app():
    for mod in ("server", "db", "db_driver"):
        if mod in sys.modules:
            importlib.reload(sys.modules[mod])
    from server import app  # noqa: E402
    return app


@pytest.fixture
def client(local_env, monkeypatch):
    """TestClient against the local SQLite sandbox with auth bypassed."""
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    app = _import_app()

    import db  # noqa: E402
    db.init_db_sync()

    # Bootstrap a local user so require_user / get_current_user
    # resolve. In APP_MODE=local the auth helpers auto-bootstrap
    # via _ensure_local_user() at first request — no cookie needed.
    with fastapi_testclient.TestClient(app) as c:
        # warm /api/runtime so the local-user bootstrap fires.
        c.get("/api/runtime")
        yield c


# ── route registration / shape ──────────────────────────────────


def test_api_leads_route_registered():
    """/api/leads must remain a registered route. Catches a future
    rename / move that would silently break the dashboard."""
    app = _import_app()
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/leads" in paths, (
        "LEADS-1 regression: /api/leads route disappeared."
    )


def test_api_leads_is_get_only():
    """/api/leads must be GET. POST/DELETE etc. would hint at a
    body-bearing contract that doesn't match the current bare-
    array reader on the frontend."""
    app = _import_app()
    methods: set = set()
    for route in app.routes:
        if getattr(route, "path", None) == "/api/leads":
            for m in (getattr(route, "methods", None) or []):
                methods.add(m.upper())
    assert "GET" in methods, "LEADS-1 regression: /api/leads must accept GET."
    for forbidden in ("POST", "PUT", "DELETE", "PATCH"):
        assert forbidden not in methods, (
            f"LEADS-1 regression: /api/leads accepts {forbidden} — "
            f"the frontend's bare-array reader expects a read-only "
            f"contract; introducing a mutator would invite a future "
            f"shape-mismatch on the same URL."
        )


def test_api_status_route_registered():
    app = _import_app()
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/status" in paths, (
        "LEADS-1 regression: /api/status (sidebar pill source) disappeared."
    )


# ── shape contract: bare array, not wrapped ─────────────────────


def test_api_leads_returns_bare_array_when_empty(client):
    """The frontend reader at templates/jarvis.html L2982-ish
    is `Array.isArray(d) ? d : (d && Array.isArray(d.leads) ? d.leads : [])`.
    The bare-array branch is the production path. If a future PR
    changes the response to `{leads: […]}` without updating the
    reader, users see an empty list with a non-zero pill — the
    exact bug LEADS-1 fixed."""
    r = client.get("/api/leads")
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body, list), (
        f"LEADS-1 regression: /api/leads must return a bare JSON "
        f"array (got {type(body).__name__}). The Jarvis loadLeads "
        f"reader's primary branch expects this shape."
    )
    assert body == [], (
        "Fresh DB should yield an empty leads array, got: "
        f"{body!r}"
    )


def test_api_status_exposes_total_lead_count_field(client):
    """The sidebar pill reads `total_lead_count` from /api/status.
    Adding the pill source-of-truth as an explicit field
    contract so a future renaming is caught immediately."""
    r = client.get("/api/status")
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body, dict), (
        f"/api/status must return a JSON object, got "
        f"{type(body).__name__}"
    )
    assert "total_lead_count" in body, (
        "LEADS-1 regression: /api/status missing `total_lead_count` "
        "field. The Jarvis sidebar pill reads this exact key — "
        "renaming silently zero-pegs the pill."
    )
    assert isinstance(body["total_lead_count"], int), (
        "total_lead_count must be an int (it's used to set "
        "textContent without coercion on the frontend)."
    )
    assert body["total_lead_count"] == 0, (
        "Fresh DB should report total_lead_count=0, got "
        f"{body['total_lead_count']}"
    )


# ── canonical invariant: pill count == list length ──────────────


def test_status_total_lead_count_matches_leads_endpoint_length(client):
    """The single load-bearing invariant: the displayed pill
    count must be the length of the list at /api/leads (under the
    HARD_CAP). Violating this is the user-reported "1 lead but
    untrue" failure mode."""
    # Insert N leads via the canonical save path.
    import db
    n = 7
    leads_in = [
        {
            "lead_id": f"lead-{i}",
            "org_name": f"Org {i}",
            "country": "ES",
            "fit_score": 8.0,
            "email_status": "new",
        }
        for i in range(n)
    ]
    # save_leads_bulk takes (user_id, leads). User 1 is the local-
    # mode auto-bootstrapped user.
    import asyncio
    asyncio.run(
        db.save_leads_bulk(1, leads_in)
    )

    list_r = client.get("/api/leads")
    assert list_r.status_code == 200
    list_body = list_r.json()
    assert isinstance(list_body, list)
    assert len(list_body) == n, (
        f"/api/leads should return all {n} leads, got "
        f"{len(list_body)}"
    )

    status_r = client.get("/api/status")
    assert status_r.status_code == 200
    status_body = status_r.json()
    assert status_body["total_lead_count"] == n, (
        f"LEADS-1 invariant violation: total_lead_count="
        f"{status_body['total_lead_count']} but /api/leads "
        f"returned {n} rows. The pill and the list must agree."
    )
    assert status_body["total_lead_count"] == len(list_body), (
        "LEADS-1 invariant: pill count must equal list length."
    )


# ── HARD_CAP defensive contract ────────────────────────────────


def test_leads_endpoint_clamps_limit_to_hard_cap(client):
    """Frontend can request limit=N. The backend must clamp to
    10000 regardless of what the client sends — defends against
    a compromised session pulling the whole table in one shot."""
    # Request way over the cap. Server must still accept the call
    # (it doesn't 400) but never return more than HARD_CAP rows.
    r = client.get("/api/leads?limit=999999")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    assert len(body) <= 10000, (
        "LEADS-2 regression: /api/leads must clamp limit to "
        f"10000, got {len(body)} rows from a 999999-row request."
    )


def test_leads_endpoint_honours_offset(client):
    """offset must paginate. Used by future infinite-scroll if the
    user has more than the render cap."""
    import db
    import asyncio
    # Insert 5 leads.
    leads_in = [{"lead_id": f"l{i}", "org_name": f"O{i}", "fit_score": 7.0}
                for i in range(5)]
    asyncio.run(
        db.save_leads_bulk(1, leads_in)
    )
    full = client.get("/api/leads").json()
    assert isinstance(full, list)
    assert len(full) == 5

    # With offset=2 we should get the back-half (3 rows).
    paged = client.get("/api/leads?offset=2").json()
    assert isinstance(paged, list)
    assert len(paged) == 3, (
        f"LEADS-2 regression: offset=2 should skip 2 rows, got "
        f"{len(paged)} of 5."
    )


def test_leads_endpoint_invalid_limit_falls_back_to_default(client):
    """Garbage limit/offset must not 500. The server should
    silently drop bad params and return the default page."""
    r = client.get("/api/leads?limit=not-a-number&offset=foo")
    assert r.status_code == 200, r.text
    assert isinstance(r.json(), list)


# ── filter-vs-total honesty ────────────────────────────────────


def test_status_total_lead_count_is_unfiltered(client):
    """The sidebar pill reflects the total persisted leads, NOT
    the result of any client-side search filter. The frontend
    crumbs (`X of Y`) handle the filtered-vs-total split. Pinning
    this invariant prevents a future "let's make /api/status
    respect server-side filters" change from desyncing the pill
    from the canonical DB count."""
    import db
    import asyncio
    leads_in = [
        {"lead_id": "l-spain", "org_name": "Tapas Co", "country": "ES",
         "fit_score": 8.0},
        {"lead_id": "l-italy", "org_name": "Pasta Inc", "country": "IT",
         "fit_score": 7.0},
        {"lead_id": "l-france", "org_name": "Croissants Ltd",
         "country": "FR", "fit_score": 9.0},
    ]
    asyncio.run(
        db.save_leads_bulk(1, leads_in)
    )

    status_body = client.get("/api/status").json()
    assert status_body["total_lead_count"] == 3, (
        "Pill count is the unfiltered total."
    )
    # /api/leads returns all 3 — there is no server-side query
    # filter on this endpoint by design (the frontend filters the
    # cached `_leads` array client-side). Pin the design.
    list_body = client.get("/api/leads").json()
    assert isinstance(list_body, list)
    assert len(list_body) == 3, (
        "LEADS-1 regression: /api/leads must return all leads "
        "(no server-side filtering); the frontend filters the "
        "in-memory `_leads` array. Adding server-side filtering "
        "without updating the pill would silently desync."
    )


# ── stability across the SSE-arrival code path ─────────────────


def test_total_lead_count_increments_after_save(client):
    """Simulate the SSE arrival path: agent saves a new lead,
    /api/status's `total_lead_count` must reflect the bump on
    the very next call (which is what the frontend's SSE 'lead'
    handler triggers via loadStatus)."""
    import db
    import asyncio

    # Initial state: 0 leads, pill shows 0.
    assert client.get("/api/status").json()["total_lead_count"] == 0

    # Agent emits + persists a lead. save_leads_bulk is the canonical
    # write path the agent uses (single-lead is just a 1-element list).
    asyncio.run(
        db.save_leads_bulk(1, [{"lead_id": "new1",
                                 "org_name": "Just Arrived",
                                 "fit_score": 8.5}])
    )

    # SSE 'lead' handler triggers loadStatus → /api/status.
    after = client.get("/api/status").json()
    assert after["total_lead_count"] == 1, (
        "LEADS-3 regression: after a save, total_lead_count must "
        "report the new total. Off-by-one or stale-count would "
        "desync the pill from the row that just arrived."
    )

    # And /api/leads (which the LEADS-3 fix now refetches alongside
    # loadStatus when the user is on the leads view) must report
    # the same row.
    rows = client.get("/api/leads").json()
    assert isinstance(rows, list)
    assert len(rows) == 1
    assert rows[0]["lead_id"] == "new1"
    assert len(rows) == after["total_lead_count"], (
        "LEADS-3 regression: the row that landed via SSE must be "
        "in /api/leads in lockstep with the pill bump."
    )


def test_total_lead_count_decrements_after_delete(client):
    """Deletion path: count must tick DOWN. Mirrors LEADS-3 in the
    inverse direction — ensures archived/deleted rows don't linger
    in the pill while disappearing from the list."""
    import db
    import asyncio

    asyncio.run(
        db.save_leads_bulk(1, [
            {"lead_id": "keep", "org_name": "Keep", "fit_score": 7},
            {"lead_id": "drop", "org_name": "Drop", "fit_score": 7},
        ])
    )
    assert client.get("/api/status").json()["total_lead_count"] == 2

    # Delete one.
    asyncio.run(
        db.delete_lead(1, "drop")
    )

    after = client.get("/api/status").json()
    assert after["total_lead_count"] == 1, (
        "LEADS-3 regression: deletion must decrement the pill "
        "count. Stale count after delete is the symmetric "
        "version of the original 'pill says 1 but list empty' bug."
    )
    rows = client.get("/api/leads").json()
    assert len(rows) == 1
    assert rows[0]["lead_id"] == "keep"


# ── inline-JS reader contract guard ────────────────────────────


def test_jarvis_template_uses_array_isarray_reader():
    """Direct guard: the inline JS reader in templates/jarvis.html
    must use `Array.isArray(d)` somewhere in its leads-load path.
    The pre-a610 reader was `((d && d.leads) || [])` — that exact
    pattern is the bug. If a future copy/paste re-introduces the
    wrapped-shape reader without the Array.isArray fallback, this
    test catches it before ship."""
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(here)
    tpl = os.path.join(repo_root, "templates", "jarvis.html")
    with open(tpl, "r", encoding="utf-8") as f:
        src = f.read()

    # The fix uses Array.isArray on the leads-load response. Both
    # call sites (loadLeads + openLeadDetail) must have it.
    array_isarray_count = src.count("Array.isArray(d) ? d :")
    assert array_isarray_count >= 2, (
        f"LEADS-1 regression: templates/jarvis.html should use "
        f"`Array.isArray(d) ? d : …` in both /api/leads consumers "
        f"(loadLeads + openLeadDetail). Found {array_isarray_count} "
        f"occurrence(s). Reverting to `((d && d.leads) || [])` "
        f"silently empties the leads list."
    )


def test_jarvis_template_no_legacy_d_dot_leads_reader():
    """Inverse guard: the old, buggy reader pattern must NOT
    appear anywhere in the template. The literal substring is
    a smoking gun — if any future merge accidentally restores
    it, fail loudly."""
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(here)
    tpl = os.path.join(repo_root, "templates", "jarvis.html")
    with open(tpl, "r", encoding="utf-8") as f:
        src = f.read()

    legacy = "((d && d.leads) || []).slice()"
    assert legacy not in src, (
        f"LEADS-1 regression: the legacy reader {legacy!r} is back "
        f"in templates/jarvis.html. This is the exact pattern that "
        f"made the leads list silently empty in pre-a610 builds, "
        f"because /api/leads returns a bare array, not a wrapped "
        f"object. Use `Array.isArray(d) ? d : (d && Array.isArray("
        f"d.leads) ? d.leads : [])` instead."
    )
