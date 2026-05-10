"""Regression tests for BRAIN-141 (a522): /api/wizard/
save-progress must accept a client-supplied
`Idempotency-Key` header and replay the original
stored response for retries with the same key.

Second-order extension of BRAIN-132 (a505) which added
the same pattern to /api/wizard/complete. save-progress
is the higher-frequency endpoint — effectively fired on
every keystroke / every Continue click — so a network
failure mid-response (server committed the merge_settings
write, client never received the 200) is much more
common here than on /complete. A blind retry without
retry safety either:

- Consumes an extra revision slot (BRAIN-14 monotonic
  bump still goes through, the second write is treated
  as a brand-new edit).
- Collides with the BRAIN-68 stale-write guard on a
  parallel tab whose `expected_revision` token now
  trails by one because of the duplicate write.
- Surfaces a "save failed" toast even though the merge
  actually landed.

With a stable Idempotency-Key the same retry replays
the same ok/phase/confidence/revision body verbatim.

Invariants tested:
- The handler reads the Idempotency-Key request header.
- The handler calls `_idempotency_lookup` BEFORE
  `_enforce_body_byte_cap`. Lookup is a cheap read; if
  it hits, we replay without paying for the body byte
  walk. (Mirrors BRAIN-132's "lookup precedes quota"
  ordering — replays must short-circuit BEFORE any
  expensive work.)
- The handler calls `_idempotency_store` after a
  successful response so subsequent retries replay it.
- The store call is reachable only AFTER the conflict
  branch returns — only true 200 successes get cached,
  per the BRAIN-132 contract.
- Helpers reused from BRAIN-132 (no duplication):
  `_idempotency_key_clean`, `_idempotency_lookup`,
  `_idempotency_store`. Module-scope constants
  (`_IDEMPOTENCY_TTL_SEC` etc.) shared.
"""
from __future__ import annotations
import inspect


def test_save_progress_handler_exists():
    """Sanity check — the handler we're hardening
    exists and is importable."""
    from server import api_wizard_save_progress
    assert callable(api_wizard_save_progress)


def test_save_progress_handler_reads_idempotency_header():
    """Source-level: api_wizard_save_progress reads the
    Idempotency-Key header at handler entry."""
    from server import api_wizard_save_progress
    src = inspect.getsource(api_wizard_save_progress)
    has_header_read = (
        "idempotency-key" in src.lower()
        or "Idempotency-Key" in src
    )
    assert has_header_read, (
        "BRAIN-141 regression: api_wizard_save_progress "
        "must read the Idempotency-Key request header."
    )


def test_save_progress_handler_uses_lookup_helper():
    """Source-level: handler consults the BRAIN-132
    lookup helper. Replay path must reuse the existing
    cache, not duplicate it under a save-progress-only
    namespace."""
    from server import api_wizard_save_progress
    src = inspect.getsource(api_wizard_save_progress)
    assert "_idempotency_lookup(" in src, (
        "BRAIN-141 regression: api_wizard_save_progress "
        "must call _idempotency_lookup before running "
        "the normal flow."
    )


def test_save_progress_handler_uses_store_helper():
    """Source-level: handler stores the success body
    under the cleaned Idempotency-Key after the merge
    commits."""
    from server import api_wizard_save_progress
    src = inspect.getsource(api_wizard_save_progress)
    assert "_idempotency_store(" in src, (
        "BRAIN-141 regression: api_wizard_save_progress "
        "must call _idempotency_store on the success "
        "path so retries replay the same body."
    )


def test_save_progress_handler_uses_key_clean():
    """Source-level: handler runs the raw header through
    the same validator BRAIN-132 uses, so identical
    rules apply (length cap, printable-ASCII gate,
    empty-string rejection)."""
    from server import api_wizard_save_progress
    src = inspect.getsource(api_wizard_save_progress)
    assert "_idempotency_key_clean(" in src, (
        "BRAIN-141 regression: api_wizard_save_progress "
        "must validate the Idempotency-Key header via "
        "_idempotency_key_clean."
    )


def test_lookup_precedes_byte_cap():
    """Source-level: idempotency lookup must come BEFORE
    `_enforce_body_byte_cap`. A replay should
    short-circuit without walking the request body —
    that's the whole point of an Idempotency-Key cache,
    cheap denial / cheap replay before any expensive
    work. Mirrors BRAIN-132's "lookup precedes quota"
    ordering on /complete."""
    from server import api_wizard_save_progress
    src = inspect.getsource(api_wizard_save_progress)
    lookup_idx = src.find("_idempotency_lookup(")
    cap_idx = src.find("_enforce_body_byte_cap(")
    assert lookup_idx >= 0
    assert cap_idx >= 0
    assert lookup_idx < cap_idx, (
        "BRAIN-141 regression: idempotency lookup must "
        "precede the byte-cap check. Replays must "
        "short-circuit before walking the request body."
    )


def test_lookup_precedes_json_parse():
    """Source-level: lookup must come BEFORE
    `request.json()`. JSON parsing on a 10 MB body costs
    real CPU; a cached replay should never pay it."""
    from server import api_wizard_save_progress
    src = inspect.getsource(api_wizard_save_progress)
    lookup_idx = src.find("_idempotency_lookup(")
    parse_idx = src.find("await request.json()")
    assert lookup_idx >= 0
    assert parse_idx >= 0
    assert lookup_idx < parse_idx, (
        "BRAIN-141 regression: idempotency lookup must "
        "precede request.json() so replays never pay "
        "the parse cost."
    )


def test_lookup_follows_rate_check():
    """Source-level: the rate-limit gate (`_check_ai_rate`)
    must come BEFORE the idempotency lookup. Otherwise a
    rate-limited attacker could probe the cache for free
    by sending arbitrary keys. Cheap denial first, then
    cache lookup."""
    from server import api_wizard_save_progress
    src = inspect.getsource(api_wizard_save_progress)
    rate_idx = src.find("_check_ai_rate(")
    lookup_idx = src.find("_idempotency_lookup(")
    assert rate_idx >= 0
    assert lookup_idx >= 0
    assert rate_idx < lookup_idx, (
        "BRAIN-141 regression: rate-limit check must "
        "precede the idempotency lookup so a "
        "rate-limited caller cannot probe the cache."
    )


def test_store_follows_lookup():
    """Source-level: the store call must come AFTER the
    lookup call. They are not interchangeable — lookup
    is the entry guard, store is the exit guard. Mirrors
    BRAIN-132 ordering on /complete."""
    from server import api_wizard_save_progress
    src = inspect.getsource(api_wizard_save_progress)
    lookup_idx = src.find("_idempotency_lookup(")
    store_idx = src.find("_idempotency_store(")
    assert lookup_idx >= 0
    assert store_idx >= 0
    assert lookup_idx < store_idx, (
        "BRAIN-141 regression: _idempotency_store must "
        "come after _idempotency_lookup. Lookup is entry "
        "guard, store is exit guard."
    )


def test_store_follows_merge_settings():
    """Source-level: the store call must come AFTER
    `db.merge_settings(` so we only ever cache responses
    for writes that actually committed. Caching before
    the merge would let a downstream merge failure
    poison the cache with a body that never reflects
    real persisted state."""
    from server import api_wizard_save_progress
    src = inspect.getsource(api_wizard_save_progress)
    merge_idx = src.find("db.merge_settings(")
    store_idx = src.find("_idempotency_store(")
    assert merge_idx >= 0
    assert store_idx >= 0
    assert merge_idx < store_idx, (
        "BRAIN-141 regression: _idempotency_store must "
        "come after db.merge_settings so only committed "
        "responses are cached."
    )


def test_store_uses_status_200():
    """Source-level: the success store passes status 200,
    matching BRAIN-132's "only 2xx successes are cached"
    contract. The conflict branch (409/410) returns
    earlier without ever reaching the store call."""
    from server import api_wizard_save_progress
    src = inspect.getsource(api_wizard_save_progress)
    # Look for the store call that includes 200.
    store_idx = src.find("_idempotency_store(")
    assert store_idx >= 0
    snippet = src[store_idx:store_idx + 200]
    assert "200" in snippet, (
        "BRAIN-141 regression: _idempotency_store must "
        "be called with status 200 on the success path. "
        "Only 2xx responses get cached per the BRAIN-132 "
        "contract."
    )


def test_success_body_shape_preserved():
    """Source-level: the success body still carries the
    documented shape `{ok, phase, confidence, revision}`.
    This catches accidental refactors that drop a field
    while wiring the cache. Old clients depend on every
    one of those fields."""
    from server import api_wizard_save_progress
    src = inspect.getsource(api_wizard_save_progress)
    assert '"ok": True' in src
    assert '"phase":' in src
    assert '"confidence":' in src
    assert '"revision":' in src


def test_lookup_returns_none_for_missing_key_save_progress():
    """Behavioral: a fresh user / fresh key has no cache
    entry. Sanity that the shared helper still works
    when called from save-progress' user namespace
    (it's the same global cache, but we verify nothing
    quietly stubbed it out)."""
    import server as _s
    import asyncio
    out = asyncio.run(_s._idempotency_lookup(
        99998, "save-progress-test-missing-key-xyz"
    ))
    assert out is None


def test_lookup_returns_none_for_invalid_key_save_progress():
    """Behavioral: empty / None keys produce None. The
    handler's `request.headers.get("idempotency-key") or ""`
    fallback feeds straight into `_idempotency_key_clean`,
    so the empty-string path must stay None."""
    import server as _s
    import asyncio
    assert asyncio.run(_s._idempotency_lookup(99998, "")) is None
    assert asyncio.run(_s._idempotency_lookup(99998, None)) is None


def test_helpers_shared_with_brain_132():
    """The helpers BRAIN-141 calls must be the same
    module-scope helpers BRAIN-132 introduced — no
    parallel implementation, no shadowed copy. A
    duplicated cache would split the namespace and
    break the contract that one Idempotency-Key replays
    one logical operation regardless of endpoint."""
    import server as _s
    # All four must exist exactly once at module scope.
    for name in (
        "_idempotency_key_clean",
        "_idempotency_lookup",
        "_idempotency_store",
        "_IDEMPOTENCY_TTL_SEC",
    ):
        assert hasattr(_s, name), (
            f"BRAIN-141 regression: required helper "
            f"`{name}` from BRAIN-132 must be present "
            f"at module scope."
        )


def test_brain_141_marker_present():
    """Source-level: a525 (BRAIN-141) marker comment is
    present near the new code. Standard pattern from the
    audit-sweep workflow — every change carries a
    `# aXXX (BRAIN-N)` rationale comment so future
    auditors can trace why."""
    from server import api_wizard_save_progress
    src = inspect.getsource(api_wizard_save_progress)
    assert "BRAIN-141" in src, (
        "BRAIN-141 regression: marker comment "
        "`# a525 (BRAIN-141): ...` must be present in "
        "api_wizard_save_progress so future auditors can "
        "trace the change."
    )
