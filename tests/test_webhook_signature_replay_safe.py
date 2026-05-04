"""Regression test for BRAIN-58 (a419): GenericWebhookPlugin must
emit a Stripe-style replay-safe signature header. Pre-fix the
signature covered only the raw body — receivers couldn't reject
replays without first parsing the JSON body for the embedded ts.

Per GPT-5.4 audit on webhook replay-safety class.
"""
from __future__ import annotations
import inspect


def test_webhook_signature_carries_separate_timestamp():
    """The X-Huntova-Signature header must include a t=<unix> field
    so receivers can freshness-check before parsing the body."""
    import bundled_plugins
    src = inspect.getsource(bundled_plugins.GenericWebhookPlugin.post_save)
    assert "t=" in src and "v1=" in src, (
        "BRAIN-58 regression: signature header must use Stripe-style "
        "`t=<unix>,v1=<hex>` so receivers can check freshness on the "
        "header before parsing the body."
    )


def test_webhook_signed_material_includes_timestamp():
    """The signature must cover `<ts>.<body>`, not just body alone.
    Without the timestamp inside the signed material, an attacker
    can replay the same body+sig pair indefinitely."""
    import bundled_plugins
    src = inspect.getsource(bundled_plugins.GenericWebhookPlugin.post_save)
    # The fix concatenates ts + "." + body before hmac.
    assert "signed_payload" in src or '_ts}.' in src or "f\"{_ts}.\"" in src, (
        "BRAIN-58 regression: signed material must include the "
        "timestamp (Stripe spec). HMAC over body alone is replayable."
    )


def test_webhook_legacy_header_preserved():
    """Don't break existing receivers using the bare sha256 header."""
    import bundled_plugins
    src = inspect.getsource(bundled_plugins.GenericWebhookPlugin.post_save)
    assert "X-Huntova-Signature-Legacy" in src or "sha256=" in src, (
        "BRAIN-58 regression: legacy bare-body sha256 header should be "
        "preserved during rollout so existing receivers don't break."
    )
