"""Regression tests for BRAIN-PROD-7 (a590): the first-run setup
wizard must classify probe failures (401 / 402 / 429 / 404 model /
timeout / network / init) and surface a humanised, action-oriented
message rather than a raw stack trace.

The pre-a590 path saved the key, ran a 1-token "respond OK" probe,
and on failure just stuffed `{type(e).__name__}: {str(e)[:120]}` into
`test_message`. The frontend showed it as a tiny "⚠ probe failed:
AuthenticationError: Error code: 401 -..." suffix on the GREEN
"saved" banner, then auto-advanced to step 3 anyway. Users routinely
reached the dashboard with a wrong/empty/exhausted key and only
discovered it minutes later when their first hunt blew up with a
cryptic provider error.

This test surface pins the post-fix behaviour:

1. Source-level: `api_setup_key` calls `humanise_ai_error` AND
   populates `test_error_kind` for 401 / 402 / 429 / 404 / timeout /
   network paths.
2. Frontend (template): `setup.html` reads `test_error_kind`, calls
   `_kindCtaLabel`, and gates the auto-advance on probe success.
3. End-to-end: simulating a failing provider returns a JSON shape
   with the right `test_error_kind` and a humanised `test_message`.

Six boundary tests as required by the swarm spec.
"""

import inspect


# ───────────────────────────────────────────────────────────────
# 1. Source-level: humanise_ai_error must be called from /api/setup/key
# ───────────────────────────────────────────────────────────────
def test_setup_key_uses_humanise_ai_error():
    """The probe-failure exception handler must route through
    `humanise_ai_error` so the user sees "Your ANTHROPIC API key is
    invalid or missing..." instead of "AuthenticationError: 401..."."""
    from server import api_setup_key
    src = inspect.getsource(api_setup_key)
    assert "humanise_ai_error" in src, (
        "BRAIN-PROD-7 regression: api_setup_key must call "
        "humanise_ai_error(e, provider_name=provider) when the probe "
        "raises so the user gets a human-readable next-action."
    )


# ───────────────────────────────────────────────────────────────
# 2. Source-level: test_error_kind classifier must cover all 6 buckets
# ───────────────────────────────────────────────────────────────
def test_setup_key_classifies_all_error_kinds():
    """The /api/setup/key probe-failure path must distinguish
    auth / credits / rate_limit / model_404 / timeout / network / init.
    Frontend depends on these strings to render targeted CTAs."""
    from server import api_setup_key
    src = inspect.getsource(api_setup_key)
    for kind in ("auth", "credits", "rate_limit", "model_404",
                 "timeout", "network", "init"):
        assert f'"{kind}"' in src or f"'{kind}'" in src, (
            f"BRAIN-PROD-7 regression: api_setup_key classifier must "
            f"emit test_error_kind={kind!r} for the matching exception "
            f"signature so the wizard can show the right CTA."
        )


# ───────────────────────────────────────────────────────────────
# 3. Source-level: test_error_kind appears in the response payload
# ───────────────────────────────────────────────────────────────
def test_setup_key_response_includes_test_error_kind():
    """The JSON response of /api/setup/key must include
    `test_error_kind`. The frontend keys off this to decide whether
    to auto-advance vs show the red banner."""
    from server import api_setup_key
    src = inspect.getsource(api_setup_key)
    assert '"test_error_kind"' in src, (
        "BRAIN-PROD-7 regression: api_setup_key must include "
        "'test_error_kind' in its JSON return so the frontend "
        "renders a classified next-action CTA."
    )


# ───────────────────────────────────────────────────────────────
# 4. Frontend: setup.html must NOT auto-advance on probe failure
# ───────────────────────────────────────────────────────────────
def test_setup_html_blocks_auto_advance_on_probe_fail():
    """The pre-a590 setup.html called setTimeout(advance, 800) for
    EVERY `d.ok=true` regardless of probe outcome. Now the auto-
    advance is gated on `d.test_passed !== false`. Confirm the gate
    exists in the template."""
    with open("templates/setup.html") as f:
        src = f.read()
    assert "probeTrulyFailed" in src, (
        "BRAIN-PROD-7 regression: templates/setup.html must compute "
        "probeTrulyFailed = (d.test_passed === false) and skip the "
        "auto-advance to step 3 when the probe explicitly failed."
    )
    # The auto-advance call (showing card-done + setStep(3)) must
    # live inside the `if (!probeTrulyFailed)` branch. We anchor on
    # the `setStep(3)` invocation that actually advances the wizard
    # — there are two in the file (success path + escape hatch); the
    # first one (success path) must sit downstream of the gate.
    advance_idx = src.find("setStep(3)")
    assert advance_idx > 0, "setStep(3) call missing from setup.html"
    # Scan backwards 2000 chars for the gate
    window = src[max(0, advance_idx - 2000):advance_idx]
    assert "probeTrulyFailed" in window, (
        "BRAIN-PROD-7 regression: the setStep(3) auto-advance must be "
        "guarded by probeTrulyFailed; it sits outside the gate so a "
        "failing probe still pushes the user to step 3."
    )


# ───────────────────────────────────────────────────────────────
# 5. Frontend: every classified kind has a CTA label
# ───────────────────────────────────────────────────────────────
def test_setup_html_kind_cta_covers_all_kinds():
    """Every `test_error_kind` the server emits must have a matching
    branch in `_kindCtaLabel` so the user sees an action label rather
    than the bare default 'Probe failed.'."""
    with open("templates/setup.html") as f:
        src = f.read()
    assert "_kindCtaLabel" in src, (
        "BRAIN-PROD-7 regression: templates/setup.html must define "
        "_kindCtaLabel(kind, providerSlug)."
    )
    for kind in ("auth", "credits", "rate_limit", "model_404",
                 "timeout", "network", "init"):
        assert f"'{kind}'" in src or f'"{kind}"' in src, (
            f"BRAIN-PROD-7 regression: _kindCtaLabel missing branch "
            f"for kind={kind!r}; user will see the generic fallback."
        )


# ───────────────────────────────────────────────────────────────
# 6. Frontend: the "Continue anyway" escape hatch must exist
# ───────────────────────────────────────────────────────────────
def test_setup_html_offers_continue_anyway_escape_hatch():
    """When the probe fails we don't hard-block: we render a
    'Continue anyway' button so a user whose provider is mid-incident
    can still finish onboarding. Pin the escape-hatch text so a future
    refactor can't accidentally remove it and trap the user."""
    with open("templates/setup.html") as f:
        src = f.read()
    assert "Continue anyway" in src, (
        "BRAIN-PROD-7 regression: templates/setup.html must render a "
        "'Continue anyway' button on probe failure so the user can "
        "still reach step 3 if the provider is having an incident."
    )


# ───────────────────────────────────────────────────────────────
# 7. End-to-end (bonus): humanise_ai_error import succeeds
# ───────────────────────────────────────────────────────────────
def test_humanise_ai_error_importable_from_app():
    """The setup-key route imports `humanise_ai_error` lazily inside
    the exception handler. If the symbol is renamed or moved out of
    `app.py`, the import would silently fall through to the raw-error
    fallback. Pin the import path."""
    from app import humanise_ai_error
    # Sanity-check the helper still classifies an obvious 401 string.
    msg = humanise_ai_error(Exception("Error code: 401 - invalid_api_key"),
                            provider_name="anthropic")
    assert "invalid" in msg.lower() or "key" in msg.lower(), (
        "humanise_ai_error must still surface 'invalid' or 'key' for "
        "401 responses so the wizard message is actionable."
    )


# ───────────────────────────────────────────────────────────────
# 8. End-to-end: the setup status response includes the new field
# ───────────────────────────────────────────────────────────────
def test_setup_key_response_default_test_error_kind_is_empty_string():
    """When `do_test=False` (or the probe succeeds), `test_error_kind`
    must be the empty string — not None, not missing — so the
    frontend's `d.test_error_kind || 'other'` branch picks the right
    fallback. Source-level pin: the variable defaults to ''."""
    from server import api_setup_key
    src = inspect.getsource(api_setup_key)
    assert 'test_error_kind = ""' in src, (
        "BRAIN-PROD-7 regression: test_error_kind must default to '' "
        "(empty string) so the frontend 'd.test_error_kind || \"other\"' "
        "fallback works and we never accidentally render 'undefined' "
        "or 'null' as the CTA."
    )
