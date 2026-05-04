"""a840: regression tests for the landing-page version self-healer.

The previous self-healer (a325) had two real bugs:

  1. It called ``r.json()`` without checking ``r.ok``. A GitHub
     rate-limit (HTTP 403) returns a parseable JSON body shaped like
     ``{"message": "API rate limit exceeded for…"}`` — no
     ``tag_name``. The old ``if (!tag) return;`` correctly bailed,
     but every page-load kept hammering api.github.com (no back-off)
     and the page kept showing whatever version was hardcoded at
     deploy time.

  2. The hardcoded baseline (``v0.1.0a324``) was 416 releases stale
     when this audit ran (current tip: a840). Any visitor whose
     fetch failed — corporate firewall, NAT-shared rate-limit, GitHub
     5xx — saw a year-old version label.

  3. No tag-format validation. A malformed ``tag_name`` would render
     literally ("vNaN", "vundefined", "vprerelease-test").

These tests pin the post-fix shape so a future template edit can't
silently regress to the old behaviour.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LANDING = ROOT / "templates" / "landing.html"


def _read_landing() -> str:
    return LANDING.read_text(encoding="utf-8")


def _extract_self_healer_script(html: str) -> str:
    """Extract the ``<script data-hv-version-autoupdate>`` body."""
    m = re.search(
        r"<script data-hv-version-autoupdate>(.*?)</script>",
        html,
        flags=re.DOTALL,
    )
    assert m, "self-healer script block missing from landing.html"
    return m.group(1)


def _strip_html_comments_and_js_line_comments(html: str) -> str:
    """Drop ``<!-- … -->`` and ``// …`` content so we only inspect
    text that actually renders / executes."""
    # Remove HTML comments (non-greedy across newlines).
    html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
    # Remove JS // line comments. We don't try to handle /* */ here —
    # the inline scripts use only line comments.
    html = re.sub(r"//[^\n]*", "", html)
    return html


def test_no_ancient_hardcoded_baseline() -> None:
    """The hardcoded fallback must not be the year-old a324 baseline.

    a325 originally shipped with ``v0.1.0a324`` baked into three
    spots (hero badge, demo terminal, trust strip). When the JS
    self-healer fails (rate-limit, offline, CSP, JS disabled), those
    are what the visitor sees. Keep the baseline rolled forward so
    a fail-closed page is at most a few releases stale.
    """
    html = _strip_html_comments_and_js_line_comments(_read_landing())
    assert "v0.1.0a324" not in html, (
        "templates/landing.html still hardcodes the ancient a324 "
        "baseline in renderable content. Bump the [data-hv-version] "
        "anchors to the current release tag so a fail-closed page "
        "(rate-limit / offline / JS-disabled) doesn't show year-old "
        "version info."
    )


def test_self_healer_script_present() -> None:
    html = _read_landing()
    assert "<script data-hv-version-autoupdate>" in html
    assert "[data-hv-version]" in html, (
        "self-healer references [data-hv-version] elements — keep at "
        "least one such anchor so the healer has somewhere to paint."
    )


def test_self_healer_checks_response_ok() -> None:
    """Without the ``r.ok`` guard, a 403 rate-limit body parses as
    JSON and the chain silently no-ops every page load."""
    js = _extract_self_healer_script(_read_landing())
    assert "r.ok" in js or "response.ok" in js, (
        "self-healer must short-circuit on non-2xx responses before "
        "calling .json() — otherwise a GitHub rate-limit body parses "
        "fine, no tag_name fires, and we never back off."
    )


def test_self_healer_validates_tag_format() -> None:
    """Avoid painting garbage like 'vNaN' / 'vundefined' / a stray
    HTML body if the upstream contract ever changes."""
    js = _extract_self_healer_script(_read_landing())
    assert "TAG_RE" in js or re.search(r"\\d\+\\\.\\d", js) or "tag_name" in js
    # The committed regex tolerates v0.1.0a324, 1.2.3, 1.2.3rc1, v2.0
    # and rejects "API rate limit exceeded", "vNaN", "<html...".
    assert re.search(r"/\^v\?\\d", js), (
        "self-healer should validate tag_name against a tag-shape "
        "regex before painting (rejects garbage / HTML bodies / "
        "rate-limit text that somehow slipped through)."
    )


def test_self_healer_has_failure_backoff() -> None:
    """A negative-cache slot prevents hammering GitHub on every
    navigation when rate-limited."""
    js = _extract_self_healer_script(_read_landing())
    assert "FAIL_KEY" in js or "noteFail" in js or "FailUntil" in js, (
        "self-healer should record a fail-until timestamp so a single "
        "bad fetch doesn't trigger a fresh request on every "
        "subsequent page load (free GitHub API quota is 60/hr/IP)."
    )


def test_self_healer_js_parses() -> None:
    """Inline JS must parse — Hostinger has no Python self-heal, this
    is the only version-update path on the marketing site."""
    js = _extract_self_healer_script(_read_landing())
    # Wrap to suppress stdout noise; only care about parse.
    out = Path("/tmp/_landing_selfhealer.js")
    out.write_text(js, encoding="utf-8")
    try:
        result = subprocess.run(
            ["node", "--check", str(out)],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except FileNotFoundError:
        pytest.skip("node not available — skipping JS parse check")
        return
    assert result.returncode == 0, (
        f"self-healer JS does not parse:\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )


def test_install_url_stable() -> None:
    """The curl|sh URL must hit ``releases/latest/download/install.sh``,
    which always resolves to the highest semver release. A direct
    /tag/<v>/ URL would 404 the moment we cut a new release."""
    html = _read_landing()
    assert "releases/latest/download/install.sh" in html
    assert "releases/tag/" not in html, (
        "landing.html must not pin install.sh to a specific tag — "
        "use releases/latest/download/install.sh so the one-liner "
        "stays valid across tag bumps."
    )


def test_og_and_twitter_card_present() -> None:
    """Open Graph + Twitter card meta tags are part of the landing
    contract for shareability."""
    html = _read_landing()
    for needle in (
        'property="og:title"',
        'property="og:description"',
        'property="og:image"',
        'name="twitter:card"',
        'name="twitter:title"',
    ):
        assert needle in html, f"missing meta tag: {needle}"


def test_self_healer_strips_to_serializable_json_cache() -> None:
    """Cache uses JSON.stringify — make sure we wrap reads in try/catch
    so a corrupt localStorage entry can't throw uncaught and break the
    page (a830 lesson from the brain wizard)."""
    js = _extract_self_healer_script(_read_landing())
    # at minimum two try/catch blocks (read + write).
    assert js.count("try {") >= 2 or js.count("try{") >= 2


def test_substitution_map_in_server_includes_current_baseline() -> None:
    """server._read_landing_with_version must rewrite the hardcoded
    baseline at render time so authenticated visitors land with the
    right version even if the JS self-healer is CSP-blocked."""
    server = (ROOT / "server.py").read_text(encoding="utf-8")
    # Find the _read_landing_with_version body.
    m = re.search(
        r"def _read_landing_with_version\([^)]*\)[^:]*:(.*?)(?=^def\s)",
        server,
        flags=re.DOTALL | re.MULTILINE,
    )
    assert m, "_read_landing_with_version not found in server.py"
    body = m.group(1)
    # The current hardcoded landing baseline must be in the
    # substitution list, otherwise the server-side heal is a no-op.
    assert "v0.1.0a840" in body or "0.1.0a840" in body, (
        "_read_landing_with_version's stale-baseline list must "
        "include the current hardcoded landing.html baseline so "
        "Python-served pages always render with the live VERSION."
    )
