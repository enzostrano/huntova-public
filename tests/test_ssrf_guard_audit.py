"""BRAIN-167: bundled_plugins SSRF guard invariant audit.

`_safe_outbound_url` is the single gate that blocks user-configured
webhook URLs from hitting cloud-metadata endpoints (169.254.169.254),
loopback, RFC1918 private ranges, link-local, IPv6 loopback, IPv4-
mapped IPv6 loopback, octal/hex/decimal-int numeric forms, CGNAT,
Alibaba metadata (100.100.100.200), and named cloud-metadata hosts.

If this gate has a hole, any user with a Slack/Discord/webhook plugin
configured can pivot the BYOK CLI into an SSRF probe of their LAN /
cloud metadata. These tests pin the gate's accept/reject contract
across the known nasty inputs.
"""
from __future__ import annotations


def test_blocks_localhost():
    from bundled_plugins import _safe_outbound_url
    assert _safe_outbound_url("http://localhost/x") is False
    assert _safe_outbound_url("https://localhost:8080/x") is False


def test_blocks_127001():
    from bundled_plugins import _safe_outbound_url
    assert _safe_outbound_url("http://127.0.0.1/") is False
    assert _safe_outbound_url("https://127.0.0.1/") is False


def test_blocks_ipv6_loopback():
    from bundled_plugins import _safe_outbound_url
    assert _safe_outbound_url("http://[::1]/") is False


def test_blocks_ipv6_loopback_named():
    from bundled_plugins import _safe_outbound_url
    assert _safe_outbound_url("http://ip6-localhost/") is False
    assert _safe_outbound_url("http://ip6-loopback/") is False


def test_blocks_aws_metadata_ip():
    from bundled_plugins import _safe_outbound_url
    assert _safe_outbound_url("http://169.254.169.254/latest/meta-data/") is False


def test_blocks_aws_metadata_named():
    from bundled_plugins import _safe_outbound_url
    assert _safe_outbound_url("http://metadata.amazonaws.com/") is False


def test_blocks_gcp_metadata_named():
    from bundled_plugins import _safe_outbound_url
    assert _safe_outbound_url("http://metadata.google.internal/") is False


def test_blocks_azure_metadata_named():
    from bundled_plugins import _safe_outbound_url
    assert _safe_outbound_url("http://metadata.azure.com/") is False


def test_blocks_instance_data():
    from bundled_plugins import _safe_outbound_url
    assert _safe_outbound_url("http://instance-data/") is False


def test_blocks_private_ranges():
    """RFC1918 — 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16."""
    from bundled_plugins import _safe_outbound_url
    assert _safe_outbound_url("http://10.0.0.1/") is False
    assert _safe_outbound_url("http://172.16.0.1/") is False
    assert _safe_outbound_url("http://192.168.1.1/") is False


def test_blocks_link_local():
    """169.254.0.0/16 — covers 169.254.169.254 already but also any
    other link-local IP."""
    from bundled_plugins import _safe_outbound_url
    assert _safe_outbound_url("http://169.254.1.1/") is False


def test_blocks_unspecified():
    """0.0.0.0 — listening-on-all-interfaces address; some OSes route
    it to localhost."""
    from bundled_plugins import _safe_outbound_url
    assert _safe_outbound_url("http://0.0.0.0/") is False


def test_blocks_octal_form_loopback():
    """`http://017700000001/` is 127.0.0.1 in octal. Python's
    ipaddress canonicalises this. Confirm the gate catches it."""
    from bundled_plugins import _safe_outbound_url
    # Note: Python urlparse may not normalize this. The gate's defense
    # comes from getaddrinfo — let's accept either reject or block.
    # The KEY invariant: if Python resolves it AS loopback, the gate
    # must block. If Python rejects parsing entirely, the gate also
    # rejects.
    result = _safe_outbound_url("http://017700000001/")
    assert result is False


def test_blocks_decimal_int_loopback():
    """`http://2130706433/` is 127.0.0.1 as a decimal int."""
    from bundled_plugins import _safe_outbound_url
    result = _safe_outbound_url("http://2130706433/")
    assert result is False


def test_blocks_cgnat_range():
    """100.64.0.0/10 is CGNAT — not private per RFC1918 but not
    globally routable either. The is_global check catches this."""
    from bundled_plugins import _safe_outbound_url
    assert _safe_outbound_url("http://100.64.0.1/") is False


def test_blocks_alibaba_metadata():
    """100.100.100.200 is Alibaba Cloud's metadata IP — outside
    RFC1918, requires the is_global check."""
    from bundled_plugins import _safe_outbound_url
    assert _safe_outbound_url("http://100.100.100.200/") is False


def test_blocks_ipv4_mapped_ipv6_loopback():
    """`::ffff:127.0.0.1` — IPv4-mapped form of loopback."""
    from bundled_plugins import _safe_outbound_url
    assert _safe_outbound_url("http://[::ffff:127.0.0.1]/") is False


def test_rejects_unknown_scheme():
    """ftp://, file://, gopher:// etc. must be rejected."""
    from bundled_plugins import _safe_outbound_url
    assert _safe_outbound_url("file:///etc/passwd") is False
    assert _safe_outbound_url("ftp://attacker.com/") is False
    assert _safe_outbound_url("gopher://x/") is False


def test_rejects_empty_url():
    from bundled_plugins import _safe_outbound_url
    assert _safe_outbound_url("") is False
    assert _safe_outbound_url(None) is False  # type: ignore[arg-type]


def test_rejects_no_host():
    from bundled_plugins import _safe_outbound_url
    assert _safe_outbound_url("http:///path") is False


def test_rejects_unparseable():
    from bundled_plugins import _safe_outbound_url
    assert _safe_outbound_url("not a url") is False


def test_rejects_dns_resolution_failure():
    """If DNS lookup fails (NXDOMAIN), refuse — better to reject a
    flaky webhook than to leak through a TTL=0 rebind."""
    from bundled_plugins import _safe_outbound_url
    # An obviously-unresolvable name. Most resolvers return NXDOMAIN
    # for `.invalid` per RFC 2606.
    assert _safe_outbound_url("http://nonexistent.invalid/") is False
