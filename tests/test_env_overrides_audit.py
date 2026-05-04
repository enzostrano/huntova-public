"""Regression tests for BRAIN-153 (a564): every numeric
config constant exposed via `os.environ.get(...)` must
follow the documented `HV_*` naming convention. Catches
typo regressions like `HUNTOVA_WIZARD_BODY_BYTES_MAX`
that would silently disable the env override + leave
operators stuck with the default forever.

Failure mode (Per Huntova engineering review on env-
override convention):

The "Pinned loosely so we don't fight the user's
global env" comment in pyproject.toml + the
HV_WIZARD_* / HV_DNA_* / HV_IDEMPOTENCY_* constants
across server.py + app.py codify the env-override
contract: every operator-tunable knob is reachable
via an env var with the `HV_` prefix.

A typo in the env-var name silently disables the
override — the operator sets `HV_WIZARD_BODY_BYTES_MAX`
but server reads `HUNTOVA_WIZARD_BODY_BYTES_MAX`,
override never takes effect, default ships.

Invariants:
- Every `os.environ.get(...)` call that drives a
  numeric constant in server.py uses an `HV_`-prefixed
  key.
- The constants module-doc-comments mention the env
  var name (so operators can grep).
"""
from __future__ import annotations
import inspect
import re


def test_env_overrides_use_hv_prefix():
    """Every os.environ.get key in server.py module
    scope follows the HV_ convention."""
    import server as _s
    src = inspect.getsource(_s)
    # Find all `os.environ.get("KEY"...)` calls at
    # module scope (anywhere in file, conservative
    # scan).
    pattern = r'os\.environ\.get\(\s*["\']([A-Z_]+)["\']'
    keys = set(re.findall(pattern, src))
    # Filter to keys that look like config (uppercase
    # only). Exclude well-known non-HV-prefixed
    # standard env vars.
    standard = {
        "DATABASE_URL", "PUBLIC_URL", "APP_MODE",
        "PORT", "HOST", "USER", "HOME", "PATH",
        "PYTHONPATH", "PWD", "SHELL", "TERM",
        "LANG", "LC_ALL", "TZ", "VIRTUAL_ENV",
        "FLY_APP_NAME", "RAILWAY_PROJECT_ID",
        "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET",
        "STRIPE_API_KEY", "STRIPE_WEBHOOK_SECRET",
        "SMTP_HOST", "SMTP_PORT", "SMTP_USER",
        "SMTP_PASS", "SMTP_FROM",
        "XDG_CONFIG_HOME",  # Linux config dir conv.
    }
    huntova_keys = keys - standard
    non_hv = {k for k in huntova_keys if not k.startswith("HV_")}
    assert not non_hv, (
        f"BRAIN-153 regression: env vars in server.py "
        f"that don't follow the HV_ prefix convention: "
        f"{non_hv}. Either add the HV_ prefix or "
        f"document them in the standard-env exclusion "
        f"list above."
    )


def test_critical_hv_env_vars_referenced():
    """The documented env-override knobs are still
    referenced. A regression that removes one would
    leave operators with no way to tune the default."""
    import server as _s
    src = inspect.getsource(_s)
    critical = {
        "HV_WIZARD_BODY_BYTES_MAX",
        "HV_WIZARD_FIELD_BYTES_MAX",
        "HV_WIZARD_PHASE5_QUESTION_BYTES_MAX",
        "HV_WIZARD_PHASE5_OPTION_BYTES_MAX",
        "HV_WIZARD_SCAN_FIELD_BYTES_MAX",
        "HV_WIZARD_PHASE_MAX",
        "HV_WIZARD_SCHEMA_VERSION",
        "HV_WIZARD_IDEMPOTENCY_TTL_SEC",
        "HV_WIZARD_IDEMPOTENCY_KEY_MAX_LEN",
        "HV_WIZARD_IDEMPOTENCY_CACHE_PER_USER_MAX",
        "HV_DNA_PENDING_STALE_SEC",
    }
    missing = {k for k in critical if k not in src}
    assert not missing, (
        f"BRAIN-153 regression: documented env-override "
        f"knobs missing from server.py: {missing}. "
        f"Operators would have no way to tune those "
        f"defaults."
    )


def test_no_legacy_huntova_prefix():
    """Catch typo regressions like `HUNTOVA_*` that
    don't follow the HV_ convention."""
    import server as _s
    src = inspect.getsource(_s)
    # Forbidden prefix: HUNTOVA_ as a config key.
    bad = re.findall(r'os\.environ\.get\(\s*["\']HUNTOVA_[A-Z_]+', src)
    assert not bad, (
        f"BRAIN-153 regression: legacy HUNTOVA_* env "
        f"keys found: {bad}. Convention is HV_*."
    )


def test_environ_get_provides_string_default():
    """Every os.environ.get call providing a numeric
    constant should also pass a default fallback (the
    `or "..."` pattern). A bare `os.environ.get(K)` with
    no default would yield None and crash on int()."""
    import server as _s
    src = inspect.getsource(_s)
    # Find HV_ env reads followed by int() or str-coerce.
    # Pattern: int(os.environ.get("HV_X") or "N")
    bad = re.findall(
        r'int\(\s*os\.environ\.get\(\s*["\']HV_[A-Z_]+["\']\s*\)\s*\)',
        src,
    )
    assert not bad, (
        f"BRAIN-153 regression: HV_ env reads passed "
        f"directly to int() without default fallback: "
        f"{bad}. Use `int(os.environ.get('HV_X') or 'N')` "
        f"to avoid None-returning crashes."
    )
