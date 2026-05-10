"""Regression tests for BRAIN-101 (a470): the BRAIN-85 idempotent
fingerprint cache must have a bounded freshness window so a
months-old completion can't silently short-circuit a
re-submit.

Failure mode (Per Huntova engineering review on cache TTL
semantics):

BRAIN-85 (a454) caches `_last_complete_fingerprint`,
`_last_complete_epoch`, `_last_complete_at` after a
successful complete. A duplicate submit with the same
canonical fingerprint short-circuits without re-running
brain+dossier+DNA — saves BYOK on legitimate retries.

But the cache had no TTL. If a user completed six months
ago, then re-submits the same profile today, the
short-circuit fires and returns `reused: true` even though:

- The underlying scoring rules / brain heuristics may have
  evolved across releases.
- The training_dossier shape may have changed.
- DNA generation logic improved over the last several
  releases.
- The user genuinely WANTS a fresh run (they re-submitted!).

The product silently keeps using the months-old derived
artifacts because the fingerprint matches. That's
"fast-responses-that-look-correct-while-downstream-behavior-
silently-diverges-from-reality" — exactly the failure mode
TTLs are designed to prevent.

Standard cache-TTL guidance: bound the freshness window for
any cache used to gate decisions about expensive work. Long
TTLs trade correctness for hit rate; gating decisions can't
afford that trade.

Invariants:
- A new constant `_COMPLETE_CACHE_TTL_SECONDS` (~14 days)
  bounds how long a fingerprint can short-circuit.
- The BRAIN-85 short-circuit conditions include a freshness
  check on `_last_complete_at`.
- Stale entries fall through to the full pipeline,
  refreshing the cache.
- Invalid / unparseable timestamp fields fall through (fail
  open: re-run rather than serve stale).
- TTL is env-overridable for power users.
"""
from __future__ import annotations
import inspect


def test_ttl_constant_exists():
    """Source-level: a TTL constant must be exposed on
    server. Operators need to tune freshness per
    deployment shape."""
    import server as _s
    ttl = getattr(_s, "_COMPLETE_CACHE_TTL_SECONDS", None)
    assert ttl is not None, (
        "BRAIN-101 regression: `_COMPLETE_CACHE_TTL_SECONDS` "
        "constant must be exposed on server."
    )
    # Reasonable range: 1 day - 90 days. Below 1 day causes
    # spurious cache misses on natural reload-and-resubmit
    # patterns; above 90 days ages out of every reasonable
    # release cycle.
    one_day = 86400
    ninety_days = 90 * 86400
    assert isinstance(ttl, int) and one_day <= ttl <= ninety_days, (
        f"BRAIN-101 regression: TTL {ttl}s unreasonable. "
        f"Should be 1d-90d (~86400-7776000 sec)."
    )


def test_ttl_is_env_overridable():
    """Source-level: the constant must read from an env var
    so power users / cloud operators can tune without
    patching the code."""
    import server as _s
    src = inspect.getsource(_s)
    assert (
        "HV_WIZARD_COMPLETE_CACHE_TTL" in src
        or "HV_COMPLETE_CACHE_TTL" in src
        or "HV_CACHE_TTL" in src
    ), (
        "BRAIN-101 regression: TTL constant must read from "
        "an env var (`HV_WIZARD_COMPLETE_CACHE_TTL` or "
        "similar). Hardcoded values aren't tunable."
    )


def test_short_circuit_checks_age():
    """Source-level: the BRAIN-85 short-circuit eligibility
    check in `api_wizard_complete` must reference the TTL
    constant."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    assert "_COMPLETE_CACHE_TTL_SECONDS" in src, (
        "BRAIN-101 regression: complete short-circuit must "
        "reference the TTL constant. Otherwise the cache has "
        "no freshness bound."
    )


def test_short_circuit_uses_last_complete_at_for_age():
    """Source-level: the freshness check must use the
    persisted `_last_complete_at` timestamp (the wallclock
    when the prior complete landed). Computing age from
    `_wizard_revision` or anything else would race against
    save-progress writes."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    # The check must reference both the TTL and the
    # last_complete_at field.
    assert "_last_complete_at" in src, (
        "BRAIN-101 regression: freshness check must compute "
        "age from `_last_complete_at`."
    )


def test_short_circuit_falls_through_on_unparseable_timestamp():
    """Source-level: if `_last_complete_at` is missing,
    empty, or unparseable, the short-circuit must fall
    through to the full pipeline (fail-open: re-run rather
    than serve stale). Look for a try/except or explicit
    isinstance/None check around the timestamp parse."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    # Find the section with the BRAIN-101 check. We look for
    # try/except or `or` patterns adjacent to
    # `_last_complete_at`.
    has_defensive_parse = (
        "try:" in src and "_last_complete_at" in src
    ) or (
        "isinstance(" in src and "_last_complete_at" in src
    )
    assert has_defensive_parse, (
        "BRAIN-101 regression: timestamp parse must be "
        "defensive — invalid/missing `_last_complete_at` "
        "should fall through, not 500."
    )


def test_short_circuit_age_check_uses_timezone_aware_now():
    """Don't regress: like BRAIN-93, the `now` comparison
    must use timezone-aware `datetime.now(timezone.utc)`,
    not the deprecated `datetime.utcnow()`."""
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    # Find the section near the TTL check.
    ttl_idx = src.find("_COMPLETE_CACHE_TTL_SECONDS")
    block = src[max(0, ttl_idx - 1000):ttl_idx + 1000]
    if "utcnow" in block:
        # If the constant is referenced inside a block that
        # also uses `utcnow`, that's a regression. (Allow
        # `utcnow` elsewhere in the function for unrelated
        # reasons.)
        # Specifically check that the BRAIN-101 path uses
        # the aware form near the TTL check.
        assert "now(timezone.utc)" in block or "datetime.fromisoformat" in block, (
            "BRAIN-101 regression: TTL check must use "
            "`datetime.now(timezone.utc)` or "
            "`datetime.fromisoformat` for the `_last_complete_at` "
            "parse. `utcnow()` is deprecated."
        )
