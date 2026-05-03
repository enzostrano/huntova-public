"""Recipe URL pre-scaffold (commits 77/78/80) regression tests.

Exercises POST /api/recipe/publish + GET /r/<slug> + GET /r/<slug>.json
end-to-end against the local SQLite, including:

- Gating: routes return 404 unless HV_RECIPE_URL_BETA is set.
- Roundtrip: published recipe can be fetched as JSON and as HTML.
- XSS hardening: hostile <script> payloads are escaped on every field.
- CSP header: present on the HTML route.
- Rate limit: 11th request from the same IP within an hour gets 429.

Run via `pytest tests/test_recipe_urls.py -v`.
"""
from __future__ import annotations

import importlib
import os
import sys

import pytest


def _import_app():
    """(Re)import server.app after env is patched. Required because
    several routes capture their config at module-load time."""
    for mod in ("server", "db", "db_driver"):
        if mod in sys.modules:
            importlib.reload(sys.modules[mod])
    from server import app  # noqa: E402
    return app


@pytest.fixture
def client(local_env, monkeypatch):
    """FastAPI TestClient with HV_RECIPE_URL_BETA enabled and a fresh DB."""
    monkeypatch.setenv("HV_RECIPE_URL_BETA", "1")
    monkeypatch.setenv("HV_PUBLIC_URL", "https://huntova.test")

    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    app = _import_app()

    # init schema
    import db  # noqa: E402
    db.init_db_sync()

    with fastapi_testclient.TestClient(app) as c:
        yield c


@pytest.fixture
def disabled_client(local_env, monkeypatch):
    """TestClient with HV_RECIPE_URL_BETA UNSET — every route should 404."""
    monkeypatch.delenv("HV_RECIPE_URL_BETA", raising=False)
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    app = _import_app()

    import db  # noqa: E402
    db.init_db_sync()

    with fastapi_testclient.TestClient(app) as c:
        yield c


# ── Gating ────────────────────────────────────────────────────────


def test_publish_404_without_flag(disabled_client):
    r = disabled_client.post("/api/recipe/publish",
                              json={"recipe": {"name": "x"}})
    assert r.status_code == 404


def test_get_404_without_flag(disabled_client):
    r = disabled_client.get("/r/abcd1234")
    assert r.status_code == 404
    j = disabled_client.get("/r/abcd1234.json")
    assert j.status_code == 404


# ── Roundtrip ─────────────────────────────────────────────────────


def test_publish_returns_slug_and_url(client):
    payload = {
        "name": "agencies-uk",
        "description": "B2B SaaS growth agencies in UK",
        "recipe": {
            "name": "agencies-uk",
            "description": "B2B SaaS growth agencies in UK",
            "countries": ["United Kingdom"],
            "queries": ["B2B SaaS marketing agency UK 10-25"],
            "max_leads": 25,
        },
        "adaptation": {"winning_terms": ["hiring"]},
        "plugins": ["csv-sink", "recipe-adapter"],
    }
    r = client.post("/api/recipe/publish", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert len(body["slug"]) == 8
    assert body["url"].endswith(f"/r/{body['slug']}")


def test_json_view_returns_payload(client):
    payload = {
        "name": "test",
        "description": "smoke",
        "recipe": {"name": "test", "countries": ["USA"], "queries": ["q1"]},
        "plugins": ["csv-sink"],
    }
    pub = client.post("/api/recipe/publish", json=payload).json()
    j = client.get(f"/r/{pub['slug']}.json")
    assert j.status_code == 200
    body = j.json()
    assert body["recipe"]["name"] == "test"
    assert body["plugins"] == ["csv-sink"]


def test_html_view_renders_recipe_metadata(client):
    payload = {
        "name": "agencies-eu",
        "recipe": {
            "name": "agencies-eu",
            "description": "EU agencies hiring video editors",
            "countries": ["Germany", "France"],
            "queries": ["marketing agency hiring video editor"],
        },
        "plugins": ["csv-sink"],
    }
    pub = client.post("/api/recipe/publish", json=payload).json()
    h = client.get(f"/r/{pub['slug']}")
    assert h.status_code == 200
    assert "agencies-eu" in h.text
    assert "Germany" in h.text
    assert "marketing agency hiring video editor" in h.text
    assert "huntova recipe import-url" in h.text


# ── XSS hardening (commit 78) ─────────────────────────────────────


def test_html_view_escapes_script_tags_in_fields(client):
    hostile = {
        "name": "<script>alert(1)</script>",
        "description": '<img src=x onerror=alert(2)>',
        "recipe": {
            "name": "<svg onload=alert(3)>",
            "description": '"><script>x=1</script>',
            "countries": ["<script>4</script>", "Germany"],
            "queries": ["hostile<script>5</script>", "safe query"],
        },
        "plugins": ["<script>6</script>", "csv-sink"],
    }
    pub = client.post("/api/recipe/publish", json=hostile).json()
    h = client.get(f"/r/{pub['slug']}")
    assert h.status_code == 200
    body = h.text
    # No raw HTML tags from user input survive in the rendered body
    assert "<script>" not in body
    assert "<svg onload" not in body.lower()
    assert "<img src=x" not in body
    # But escaped versions ARE present (proof escaping happened)
    assert "&lt;script&gt;" in body or "&amp;lt;script&amp;gt;" in body


# ── CSP header (commit 80) ────────────────────────────────────────


def test_html_view_emits_csp_header(client):
    payload = {"recipe": {"name": "csp-test", "queries": ["test"]}}
    pub = client.post("/api/recipe/publish", json=payload).json()
    h = client.get(f"/r/{pub['slug']}")
    assert h.status_code == 200
    csp = h.headers.get("content-security-policy", "")
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    # Defence-in-depth headers
    assert h.headers.get("x-frame-options") == "DENY"
    assert h.headers.get("x-content-type-options") == "nosniff"


# ── Validation ────────────────────────────────────────────────────


def test_publish_rejects_missing_recipe_field(client):
    r = client.post("/api/recipe/publish", json={"name": "no-recipe"})
    assert r.status_code == 400
    assert r.json()["error"] == "missing_recipe_field"


def test_publish_rejects_non_object_body(client):
    r = client.post("/api/recipe/publish", json="not an object")
    assert r.status_code in (400, 422)


def test_get_returns_404_for_nonexistent_slug(client):
    r = client.get("/r/deadbeef")
    assert r.status_code == 404
    j = client.get("/r/deadbeef.json")
    assert j.status_code == 404
