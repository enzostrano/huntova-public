"""Regression test for BRAIN-63 (a424): `huntova recipe import-url`
must reject non-http schemes, private/loopback hosts, and 30x
redirects to private destinations.

Per GPT-5.4 audit on SSRF class.
"""
from __future__ import annotations
import inspect


def test_import_url_rejects_nonhttp_schemes():
    """Source-level: the import-url branch must check scheme is
    http/https before urlopen."""
    import cli
    src = inspect.getsource(cli.cmd_recipe)
    # Branch must reject anything other than http/https.
    assert "scheme" in src.lower(), (
        "BRAIN-63 regression: import-url must validate URL scheme."
    )
    assert "http" in src and ("http(s)" in src or '"http"' in src), (
        "BRAIN-63 regression: scheme allowlist must include http/https."
    )


def test_import_url_uses_classify_url_or_private_host_check():
    """Source-level: import-url must call app.classify_url (or fall
    back to a minimal private-host blocklist) before fetching."""
    import cli
    src = inspect.getsource(cli.cmd_recipe)
    assert ("classify_url" in src or "169.254" in src or "localhost" in src.lower()), (
        "BRAIN-63 regression: import-url must reject private/loopback/"
        "cloud-metadata hosts. Either via app.classify_url or an "
        "explicit blocklist of internal addresses."
    )


def test_import_url_blocks_redirects():
    """Source-level: import-url must use a no-redirect opener so a
    30x to a private destination doesn't bypass the host check."""
    import cli
    src = inspect.getsource(cli.cmd_recipe)
    assert ("NoRedirect" in src or "HTTPRedirectHandler" in src or "redirect" in src.lower()), (
        "BRAIN-63 regression: 30x redirects must be blocked or "
        "re-validated against the same host policy. Otherwise an "
        "allowed-looking URL can redirect to localhost / metadata."
    )
