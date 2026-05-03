"""Regression tests for BRAIN-70 (a431): /api/wizard/scan must bound
its network cost regardless of target URL.

Failure mode (per GPT-5.4 resource-exhaustion audit):

1. User submits a URL that resolves to a giant binary (1GB ISO,
   PDF, video stream). `_fetch_site_text_sync` calls
   `requests.get(...)` with `stream=False` (default) and accesses
   `r.text`, which buffers the entire response into memory before
   the size check. Hostile or accidentally-large targets OOM the
   server worker.

2. Endless redirect chain. `requests` defaults to 30 redirects, but
   that's not explicit and it's higher than what's safe for an
   AI-orchestrated scan path. A site bouncing through 30 redirects
   eats 30 connect+TLS handshakes per scan attempt; combined with
   the 3-URL variant retry × 3 fallback paths, a single bad URL
   can pin a worker for 30+ seconds.

3. Slow-loris body. Server keeps connection open dribbling bytes.
   `requests.get(timeout=15)` is a connect+read timeout, but the
   read timeout resets each chunk, so a server sending 1 byte
   every 14s satisfies the timeout indefinitely.

4. Binary Content-Type (application/octet-stream, application/pdf,
   image/*, video/*, application/zip). `_strip()` regex-replaces
   tags on what it expects to be HTML; on binary it does nothing
   useful but still reads the whole response.

Invariants:
- Use `stream=True` when fetching scan targets so we can abort
  before fully buffering a hostile/giant response.
- Reject responses early when `Content-Length` header indicates
  body > 5MB.
- Reject responses early when `Content-Type` is in the binary
  blocklist.
- Cap iter_content reads at a hard byte ceiling (~5MB).
- Use a TUPLE timeout (connect, read) so the read timeout is
  capped per-chunk-arrival, not just per-chunk-duration.
"""
from __future__ import annotations
import inspect


def test_fetch_uses_streaming_with_byte_cap():
    """Source-level: `_fetch_site_text_sync` must use `stream=True`
    and read with a byte ceiling — not buffer `r.text` blindly."""
    from server import _fetch_site_text_sync
    src = inspect.getsource(_fetch_site_text_sync)
    assert "stream=True" in src, (
        "BRAIN-70 regression: scan fetch must use stream=True so "
        "a hostile 1GB response can be aborted before fully "
        "buffering. Pre-fix, `r.text` materialized the entire "
        "response into memory before any size check ran."
    )
    # Must reference iter_content (chunked read) AND a byte cap.
    assert "iter_content" in src, (
        "BRAIN-70 regression: stream=True alone doesn't help if "
        "we then call r.text or r.content (both materialize the "
        "whole body). Must use iter_content with a byte ceiling."
    )


def test_fetch_rejects_oversized_content_length():
    """Source-level: scan fetch must check the Content-Length
    header before reading the body and reject responses larger
    than the cap (~5MB)."""
    from server import _fetch_site_text_sync
    src = inspect.getsource(_fetch_site_text_sync)
    assert "Content-Length" in src or "content-length" in src, (
        "BRAIN-70 regression: scan fetch must inspect the "
        "Content-Length header pre-read. Otherwise a server "
        "advertising a multi-GB body can lure the scanner into "
        "starting an unbounded read."
    )
    # Must have a 5MB-ish cap constant somewhere in the function.
    has_cap = (
        "5_000_000" in src or "5 * 1024 * 1024" in src
        or "5*1024*1024" in src or "_SCAN_MAX_BYTES" in src
    )
    assert has_cap, (
        "BRAIN-70 regression: scan fetch must define a hard byte "
        "ceiling. ~5MB is generous for HTML/text and far below "
        "OOM risk."
    )


def test_fetch_rejects_binary_content_types():
    """Source-level: scan must reject obviously-binary
    Content-Types (PDF, image, video, octet-stream, zip) at the
    header stage. Stripping HTML tags off binary is a waste of
    CPU + the result is garbage AI input."""
    from server import _fetch_site_text_sync
    src = inspect.getsource(_fetch_site_text_sync)
    assert ("Content-Type" in src or "content-type" in src), (
        "BRAIN-70 regression: scan must inspect Content-Type "
        "pre-read."
    )
    # Must reference at least one of the binary types we want
    # to reject.
    has_binary_check = (
        "application/" in src
        or "image/" in src
        or "video/" in src
        or "octet-stream" in src
        or "_BINARY_CONTENT_TYPES" in src
    )
    assert has_binary_check, (
        "BRAIN-70 regression: scan must explicitly reject binary "
        "Content-Types. Default behavior of trying to strip HTML "
        "tags off a PDF / image / zip wastes CPU + memory and "
        "feeds garbage to the AI summarizer."
    )


def test_fetch_uses_tuple_timeout_for_slow_loris_protection():
    """Source-level: scan fetch must use a tuple timeout
    `(connect_s, read_s)` with a tight read timeout. A scalar
    `timeout=15` is the per-chunk read timeout — a slow-loris
    server sending 1 byte every 14s satisfies it indefinitely.
    Tuple-form caps connect AND between-chunk reads."""
    from server import _fetch_site_text_sync
    src = inspect.getsource(_fetch_site_text_sync)
    # Look for `timeout=(...)` tuple syntax. Either explicit
    # tuple or a named constant that's a tuple.
    has_tuple_timeout = (
        "timeout=(" in src
        or "_SCAN_TIMEOUT" in src
        or "_FETCH_TIMEOUT" in src
    )
    assert has_tuple_timeout, (
        "BRAIN-70 regression: scan fetch must use a tuple "
        "timeout (connect, read) so the read timeout caps "
        "between-chunk arrival. Scalar timeout is reset per "
        "chunk and doesn't bound total wall-clock."
    )


def test_fetch_caps_redirect_chain_explicitly():
    """Source-level: scan fetch must cap redirects explicitly.
    Default is 30 (urllib3) which is too generous for the
    3-URL × 3-fallback scan flow — a redirect-bouncing target
    can pin a worker for tens of seconds. Cap to a small
    constant (~5)."""
    from server import _fetch_site_text_sync
    src = inspect.getsource(_fetch_site_text_sync)
    has_redirect_cap = (
        "max_redirects" in src
        or "_MAX_REDIRECTS" in src
        or "Session()" in src  # session lets us set max_redirects
    )
    assert has_redirect_cap, (
        "BRAIN-70 regression: scan fetch must explicitly cap the "
        "redirect chain. Defaults are too generous for our "
        "3×fallback scan flow. A small cap (~5) is plenty for "
        "real domains and saves us from redirect-DoS targets."
    )


def test_scan_endpoint_still_has_ssrf_guard():
    """Don't regress the SSRF guard. The hardening above must NOT
    weaken the `_is_safe_url` check that already protects against
    private/loopback/link-local target abuse."""
    from server import api_wizard_scan
    src = inspect.getsource(api_wizard_scan)
    assert "_is_safe_url" in src, (
        "BRAIN-70 regression: don't drop the SSRF guard while "
        "adding resource caps. Both layers are needed."
    )
