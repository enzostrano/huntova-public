"""Public-share /h/<slug> regression tests (a1120).

Wave-3 swarm audit found four real bugs on the public-share surface:

1. `public_share_enabled` capability flag was declared but never enforced
   at the route layer — admins setting HV_PUBLIC_SHARE=0 still saw shares
   served.
2. `db.create_hunt_share` had no PRIMARY KEY collision retry — a slug
   re-roll on `secrets.token_urlsafe(8)` would 500 the user instead
   of trying again. Astronomically unlikely but still a correctness bug.
3. `db.get_hunt_share` always bumped `view_count`, so Slack/Twitter
   unfurls hitting `/h/<slug>/og.svg` and CLI fork pulls of
   `/h/<slug>.json` were silently inflating the share owner's analytics.
4. `/h/<slug>.json` had no X-Robots-Tag header (HTML route had a meta
   tag but JSON never gets one).

These tests pin all four fixes.
"""
from __future__ import annotations

import asyncio
import importlib
import sys
from datetime import datetime, timezone

import pytest


def _import_app():
    for mod in ("server", "db", "db_driver", "runtime"):
        if mod in sys.modules:
            importlib.reload(sys.modules[mod])
    from server import app  # noqa: E402
    return app


def _seed_share(slug: str, user_id: int = 1, snapshot: str = '{"leads":[],"meta":{}}',
                title: str = "t") -> None:
    """Insert a hunt_share row directly, bypassing the create_hunt_share
    helper so tests can pin a specific slug."""
    import db
    sql = ("INSERT INTO hunt_shares (slug, user_id, run_id, snapshot, "
           "title, created_at) VALUES (%s, %s, %s, %s, %s, %s)")
    params = [slug, user_id, None, snapshot, title,
              datetime.now(timezone.utc).isoformat()]
    runner = getattr(db, "_aexec")

    async def _run():
        await runner(sql, params)
    asyncio.run(_run())


def _read_view_count(slug: str) -> int:
    import db
    fetch = getattr(db, "_afetchone")

    async def _run():
        return await fetch(
            "SELECT view_count FROM hunt_shares WHERE slug = %s", [slug])
    row = asyncio.run(_run())
    return int((row or {}).get("view_count") or 0)


@pytest.fixture
def client(local_env, monkeypatch):
    monkeypatch.setenv("HV_PUBLIC_URL", "https://huntova.test")
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    app = _import_app()
    import db  # noqa: E402
    db.init_db_sync()
    with fastapi_testclient.TestClient(app) as c:
        yield c


@pytest.fixture
def disabled_client(local_env, monkeypatch):
    """Client with HV_PUBLIC_SHARE=0 — every share route must 404."""
    monkeypatch.setenv("HV_PUBLIC_SHARE", "0")
    monkeypatch.setenv("HV_PUBLIC_URL", "https://huntova.test")
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    app = _import_app()
    import db  # noqa: E402
    db.init_db_sync()
    with fastapi_testclient.TestClient(app) as c:
        yield c


# ── Capability gate (bug 1) ───────────────────────────────────────


def test_share_html_404_when_disabled(disabled_client):
    r = disabled_client.get("/h/abcdefg1")
    assert r.status_code == 404


def test_share_json_404_when_disabled(disabled_client):
    r = disabled_client.get("/h/abcdefg1.json")
    assert r.status_code == 404


def test_share_og_404_when_disabled(disabled_client):
    r = disabled_client.get("/h/abcdefg1/og.svg")
    assert r.status_code == 404


def test_share_views_404_when_disabled(disabled_client):
    r = disabled_client.get("/api/share/abcdefg1/views")
    assert r.status_code == 404


def test_try_404_when_disabled(disabled_client):
    r = disabled_client.post("/api/try", json={"icp": "x" * 50})
    assert r.status_code == 404


# ── Slug collision retry (bug 2) ──────────────────────────────────


def test_create_hunt_share_retries_on_collision(local_env, monkeypatch):
    """Force a slug clash on the first roll; second roll must succeed."""
    for mod in ("runtime", "db_driver", "db"):
        if mod in sys.modules:
            importlib.reload(sys.modules[mod])
    import db  # noqa: E402
    import secrets as _sec
    db.init_db_sync()

    fixed_slug = "collide-me"
    _seed_share(fixed_slug, user_id=1, title="seed")

    calls = {"n": 0}
    real_token = _sec.token_urlsafe

    def fake_token(nbytes):
        calls["n"] += 1
        if calls["n"] == 1:
            return fixed_slug
        return real_token(nbytes)

    monkeypatch.setattr(db.secrets, "token_urlsafe", fake_token)

    creator = getattr(db, "create_hunt_share")

    async def _create():
        return await creator(
            user_id=2, run_id=None, leads=[{"org_name": "x"}],
            hunt_meta={}, title="retry test")

    new_slug = asyncio.run(_create())
    assert new_slug != fixed_slug
    assert calls["n"] >= 2


# ── view_count read-purity (bug 3) ────────────────────────────────


def test_og_svg_does_not_bump_views(client, local_env):
    slug = "ogcountest"
    _seed_share(slug, snapshot='{"leads":[{"org_name":"X","fit_score":8}],"meta":{"icp":"x"}}')
    for _ in range(5):
        r = client.get(f"/h/{slug}/og.svg")
        assert r.status_code == 200
    assert _read_view_count(slug) == 0


def test_json_does_not_bump_views(client, local_env):
    slug = "jsoncntst"
    _seed_share(slug, snapshot='{"leads":[{"org_name":"X"}],"meta":{}}')
    for _ in range(3):
        r = client.get(f"/h/{slug}.json")
        assert r.status_code == 200
    assert _read_view_count(slug) == 0


def test_html_does_bump_views(client, local_env):
    slug = "htmlcntst"
    _seed_share(slug, snapshot='{"leads":[{"org_name":"X"}],"meta":{}}')
    for _ in range(3):
        r = client.get(f"/h/{slug}", headers={"User-Agent": "Mozilla/5.0"})
        assert r.status_code == 200
    assert _read_view_count(slug) == 3


# ── X-Robots-Tag on JSON (bug 4) ──────────────────────────────────


def test_json_endpoint_has_noindex_header(client, local_env):
    slug = "robotstst"
    _seed_share(slug)
    r = client.get(f"/h/{slug}.json")
    assert r.status_code == 200
    xrobots = r.headers.get("x-robots-tag", "").lower()
    assert "noindex" in xrobots
    assert "nofollow" in xrobots


# ── HTML noindex meta tag still present (regression guard) ────────


def test_html_share_emits_noindex_meta(client, local_env):
    slug = "metarobots"
    _seed_share(slug)
    r = client.get(f"/h/{slug}")
    assert r.status_code == 200
    body = r.text.lower()
    assert "name='robots'" in body or 'name="robots"' in body
    assert "noindex" in body


# ── PII leakage guard (defence-in-depth) ──────────────────────────


def test_share_lead_fields_exclude_pii(client):
    from server import _SHARE_LEAD_FIELDS  # noqa: E402
    forbidden = {
        "contact_email", "contact_phone", "contact_name",
        "notes", "_is_generic_email", "_data_confidence",
        "_confidence_signals", "wizard_data", "icp_description",
        "user_email", "internal_score", "scoring_rationale",
    }
    leak = forbidden & set(_SHARE_LEAD_FIELDS)
    assert not leak, f"_SHARE_LEAD_FIELDS leaks PII fields: {leak}"


def test_share_json_strips_user_id(client, local_env):
    slug = "useridtst"
    _seed_share(slug, user_id=42)
    r = client.get(f"/h/{slug}.json")
    assert r.status_code == 200
    body = r.json()
    assert "user_id" not in body.get("share", {})


# ── Invalid slug shape ────────────────────────────────────────────


def test_invalid_slug_404s_on_all_routes(client):
    bad = "x"  # too short (<4 chars)
    for path in (f"/h/{bad}", f"/h/{bad}.json", f"/h/{bad}/og.svg",
                 f"/api/share/{bad}/views"):
        r = client.get(path)
        assert r.status_code == 404, f"{path} returned {r.status_code}"
