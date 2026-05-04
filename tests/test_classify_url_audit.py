"""BRAIN-195: app.classify_url + is_private_url SSRF gate audit.

`classify_url` is the SSRF gate for URL fetching in app.py (wizard
scan, research re-scrape, lead URL crawls). Complement to
bundled_plugins._safe_outbound_url (BRAIN-167). Returns 4-state
classification ("ok" / "private" / "unresolvable" / "malformed").

Pinned invariants (similar gauntlet to BRAIN-167 but on the
agent-fetch surface):
"""
from __future__ import annotations


def test_classify_url_localhost_private():
    from app import classify_url
    assert classify_url("http://localhost/") == "private"
    assert classify_url("https://localhost:8080/") == "private"


def test_classify_url_127001_private():
    from app import classify_url
    assert classify_url("http://127.0.0.1/") == "private"


def test_classify_url_ipv6_loopback_private():
    from app import classify_url
    assert classify_url("http://[::1]/") == "private"


def test_classify_url_named_ipv6_loopback_private():
    from app import classify_url
    assert classify_url("http://ip6-localhost/") == "private"
    assert classify_url("http://ip6-loopback/") == "private"


def test_classify_url_aws_metadata_private():
    from app import classify_url
    assert classify_url("http://169.254.169.254/latest/meta-data/") == "private"


def test_classify_url_rfc1918_private():
    from app import classify_url
    assert classify_url("http://10.0.0.1/") == "private"
    assert classify_url("http://172.16.0.1/") == "private"
    assert classify_url("http://192.168.1.1/") == "private"


def test_classify_url_link_local_private():
    from app import classify_url
    assert classify_url("http://169.254.1.1/") == "private"


def test_classify_url_unspecified_private():
    from app import classify_url
    assert classify_url("http://0.0.0.0/") == "private"


def test_classify_url_dot_local_suffix_private():
    """`.local` is the mDNS suffix — block."""
    from app import classify_url
    assert classify_url("http://printer.local/") == "private"


def test_classify_url_dot_localhost_suffix_private():
    """`*.localhost` is RFC 6761 reserved — block."""
    from app import classify_url
    assert classify_url("http://test.localhost/") == "private"


def test_classify_url_ipv4_mapped_ipv6_loopback_private():
    """Audit wave 29 fix: `[::ffff:127.0.0.1]` unwrapped to v4 →
    detected as loopback. Pre-fix, IPv6Address.is_loopback was False
    for the mapped form, bypassing the gate."""
    from app import classify_url
    assert classify_url("http://[::ffff:127.0.0.1]/") == "private"


def test_classify_url_malformed_returns_malformed():
    """Empty / no-host URL."""
    from app import classify_url
    assert classify_url("") == "malformed"
    assert classify_url("http:///path") == "malformed"
    assert classify_url("not-a-url") == "malformed"


def test_classify_url_unresolvable():
    """A name that doesn't resolve → 'unresolvable'."""
    from app import classify_url
    # `.invalid` per RFC 2606.
    assert classify_url("http://nonexistent.invalid/") == "unresolvable"


def test_classify_url_empty_host():
    from app import classify_url
    # url with empty host.
    assert classify_url("http:///x") == "malformed"


def test_is_private_url_true_for_non_ok():
    """Backwards-compat wrapper: True for everything except 'ok'."""
    from app import is_private_url
    assert is_private_url("http://localhost/") is True
    assert is_private_url("http://10.0.0.1/") is True
    assert is_private_url("") is True  # malformed → True (fail-closed)
    assert is_private_url("http://nonexistent.invalid/") is True  # unresolvable → True


def test_classify_url_handles_uppercase_host():
    """Hostnames are case-insensitive."""
    from app import classify_url
    assert classify_url("http://LOCALHOST/") == "private"
    assert classify_url("http://127.0.0.1/") == "private"


def test_classify_url_brackets_stripped():
    """IPv6 in URL is wrapped in `[..]`; the gate must strip them
    before parsing."""
    from app import classify_url
    assert classify_url("http://[::1]/") == "private"


def test_classify_url_handles_exception():
    """If parsing or socket call raises in an unexpected way, must
    return 'private' (fail-closed) rather than crash."""
    from app import classify_url
    # Pass something that may break various parts.
    out = classify_url("http://" + "a" * 10000)
    # Either malformed, unresolvable, or private — never 'ok' for
    # something this weird.
    assert out != "ok"
