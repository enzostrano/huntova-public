"""Regression test for BRAIN-59 (a420): /h/<slug> share page renders
lead `org_website` into an href attribute. Pre-fix the value was
only html.escape()'d — which doesn't strip `javascript:` schemes.
A malicious AI-extracted org_website like
`javascript:alert(document.cookie)` would land as a working
clickable XSS on the public no-auth share page.

Per GPT-5.4 audit on DOM XSS class.
"""
from __future__ import annotations


def _share_with_url(url: str):
    return {
        "title": "test",
        "leads": [{"org_name": "Acme", "org_website": url, "fit_score": 8}],
        "meta": {},
        "slug": "abcd1234",
    }


def test_javascript_scheme_url_neutralized():
    from server import _render_share_page
    html = _render_share_page(_share_with_url("javascript:alert(1)"))
    # The dangerous URL must NOT appear in any href.
    assert 'href=\'javascript:' not in html and 'href="javascript:' not in html, (
        "BRAIN-59 regression: javascript: URLs must be scrubbed from "
        "href attributes — html.escape() alone does not prevent the "
        "javascript: scheme from being clickable."
    )


def test_data_uri_url_neutralized():
    """Same class — data: URIs in href can also execute (some browsers)."""
    from server import _render_share_page
    html = _render_share_page(_share_with_url("data:text/html,<script>alert(1)</script>"))
    assert 'href=\'data:' not in html and 'href="data:' not in html


def test_clean_https_url_passes():
    """Don't regress legitimate URLs — they should still render."""
    from server import _render_share_page
    html = _render_share_page(_share_with_url("https://example.com/about"))
    assert "example.com" in html
