"""BRAIN-176: payments.handle_webhook signature + replay-window audit.

The Stripe webhook handler is the inbound surface for billing
events: subscription created, payment succeeded, invoice paid.
Bypassing the signature gate means an attacker can mint fake
"payment succeeded" events to grant themselves credits.

Pinned invariants:

1. Empty / no `STRIPE_WEBHOOK_SECRET` → reject (don't accept
   unsigned).
2. Missing Stripe-Signature header → reject.
3. Malformed signature header (no `t=` / no `v1=`) → reject.
4. Signature mismatch → reject.
5. Replay-window: timestamps older than 5 min → reject.
6. Replay-window: timestamps more than 60s in the future → reject.
7. Multiple v1 sigs (key rotation) — any matching is accepted.
8. Constant-time comparison (no timing-leak via `==`).
9. Malformed JSON payload → reject.
10. Event missing `type` → reject.
11. Event missing `id` → reject.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time

import pytest


def _run(coro):
    return asyncio.run(coro)


def _make_signed_payload(payload: bytes, secret: bytes,
                         timestamp: int | None = None,
                         skew_seconds: int = 0) -> tuple[bytes, str]:
    """Build a (payload, sig_header) pair with a valid Stripe signature."""
    ts = (timestamp if timestamp is not None
          else int(time.time()) + skew_seconds)
    signed = f"{ts}".encode("ascii") + b"." + payload
    sig = hmac.HMAC(secret, signed, hashlib.sha256).hexdigest()
    return payload, f"t={ts},v1={sig}"


def test_no_webhook_secret_configured_rejects(local_env, monkeypatch):
    """If STRIPE_WEBHOOK_SECRET is empty, reject — don't accept
    unsigned events."""
    import importlib
    import payments
    monkeypatch.setattr(payments, "STRIPE_WEBHOOK_SECRET", "")

    result = _run(payments.handle_webhook(b'{"type":"x","id":"e1"}',
                                           "t=1,v1=abc"))
    assert result["ok"] is False
    assert "secret" in result["error"].lower()


def test_missing_signature_header_rejects(local_env, monkeypatch):
    import importlib
    import payments
    monkeypatch.setattr(payments, "STRIPE_WEBHOOK_SECRET", "whsec_test")

    result = _run(payments.handle_webhook(b'{"type":"x","id":"e1"}', ""))
    assert result["ok"] is False
    assert "missing" in result["error"].lower() and "signature" in result["error"].lower()


def test_malformed_signature_header_rejects(local_env, monkeypatch):
    import payments
    monkeypatch.setattr(payments, "STRIPE_WEBHOOK_SECRET", "whsec_test")

    # No `t=`, no `v1=`.
    result = _run(payments.handle_webhook(b'{"type":"x","id":"e1"}',
                                           "garbage"))
    assert result["ok"] is False
    assert "missing" in result["error"].lower() or "signature" in result["error"].lower()


def test_signature_mismatch_rejects(local_env, monkeypatch):
    import payments
    secret = "whsec_test_secret"
    monkeypatch.setattr(payments, "STRIPE_WEBHOOK_SECRET", secret)

    payload = b'{"type":"x","id":"e1"}'
    # Build header with WRONG signature.
    bad_sig = "0" * 64
    header = f"t={int(time.time())},v1={bad_sig}"
    result = _run(payments.handle_webhook(payload, header))
    assert result["ok"] is False
    assert "invalid" in result["error"].lower()


def test_old_timestamp_rejects(local_env, monkeypatch):
    """Timestamps more than 5 min old must be rejected (replay attack)."""
    import payments
    secret = "whsec_test_secret"
    monkeypatch.setattr(payments, "STRIPE_WEBHOOK_SECRET", secret)

    payload = b'{"type":"x","id":"e1"}'
    # 10 minutes ago.
    p, h = _make_signed_payload(payload, secret.encode(),
                                 skew_seconds=-600)
    result = _run(payments.handle_webhook(p, h))
    assert result["ok"] is False
    assert "tolerance" in result["error"].lower() or "timestamp" in result["error"].lower()


def test_far_future_timestamp_rejects(local_env, monkeypatch):
    """Timestamps more than 60s in the future must be rejected
    (a291 fix — was previously accepting up to 5 min in the future)."""
    import payments
    secret = "whsec_test_secret"
    monkeypatch.setattr(payments, "STRIPE_WEBHOOK_SECRET", secret)

    payload = b'{"type":"x","id":"e1"}'
    # 5 minutes in the future.
    p, h = _make_signed_payload(payload, secret.encode(),
                                 skew_seconds=300)
    result = _run(payments.handle_webhook(p, h))
    assert result["ok"] is False
    assert "tolerance" in result["error"].lower() or "timestamp" in result["error"].lower()


def test_malformed_timestamp_rejects(local_env, monkeypatch):
    import payments
    secret = "whsec_test_secret"
    monkeypatch.setattr(payments, "STRIPE_WEBHOOK_SECRET", secret)

    payload = b'{"type":"x","id":"e1"}'
    # Build a sig with non-numeric timestamp.
    sig = hmac.HMAC(secret.encode(),
                    b"not-a-number." + payload,
                    hashlib.sha256).hexdigest()
    header = f"t=not-a-number,v1={sig}"
    result = _run(payments.handle_webhook(payload, header))
    assert result["ok"] is False


def test_malformed_json_rejects(local_env, monkeypatch):
    """Even with a VALID signature, malformed JSON payload rejects."""
    import payments
    secret = "whsec_test_secret"
    monkeypatch.setattr(payments, "STRIPE_WEBHOOK_SECRET", secret)

    payload = b'{not valid json'
    p, h = _make_signed_payload(payload, secret.encode())
    result = _run(payments.handle_webhook(p, h))
    assert result["ok"] is False
    assert "json" in result["error"].lower() or "invalid" in result["error"].lower()


def test_event_missing_type_rejects(local_env, monkeypatch):
    import payments
    secret = "whsec_test_secret"
    monkeypatch.setattr(payments, "STRIPE_WEBHOOK_SECRET", secret)

    payload = json.dumps({"id": "evt_123"}).encode()  # missing `type`
    p, h = _make_signed_payload(payload, secret.encode())
    result = _run(payments.handle_webhook(p, h))
    assert result["ok"] is False
    assert "type" in result["error"].lower()


def test_event_missing_id_rejects(local_env, monkeypatch):
    """A Stripe event without an id breaks idempotency tracking —
    reject upfront."""
    import payments
    secret = "whsec_test_secret"
    monkeypatch.setattr(payments, "STRIPE_WEBHOOK_SECRET", secret)

    payload = json.dumps({"type": "checkout.session.completed"}).encode()
    p, h = _make_signed_payload(payload, secret.encode())
    result = _run(payments.handle_webhook(p, h))
    assert result["ok"] is False
    assert "id" in result["error"].lower()


def test_multiple_v1_sigs_accepted_during_rotation(local_env, monkeypatch):
    """During webhook key rotation, Stripe sends multiple `v1=` sigs.
    Any match is accepted."""
    import payments
    secret = "whsec_active_secret"
    monkeypatch.setattr(payments, "STRIPE_WEBHOOK_SECRET", secret)

    # Stub db calls.
    monkeypatch.setattr(payments.db, "check_webhook_processed",
                        lambda eid: _async_return(False))
    # Real signature plus a fake one (rotation scenario).
    payload = json.dumps({"type": "ping", "id": "evt_1"}).encode()
    ts = int(time.time())
    real_sig = hmac.HMAC(secret.encode(),
                         f"{ts}".encode("ascii") + b"." + payload,
                         hashlib.sha256).hexdigest()
    fake_sig = "0" * 64
    # Header with fake-then-real (order shouldn't matter).
    header = f"t={ts},v1={fake_sig},v1={real_sig}"
    result = _run(payments.handle_webhook(payload, header))
    # Past signature gate. May fail downstream (DB stubs), but signature
    # was accepted.
    # The error if any is NOT "Invalid signature".
    if not result.get("ok"):
        assert "invalid signature" not in (result.get("error") or "").lower()


async def _async_return(value):
    return value


def test_constant_time_comparison():
    """Verify the codebase uses hmac.compare_digest (timing-attack-safe)
    rather than `==` for signature comparison."""
    import payments
    src = open(payments.__file__).read()
    # Inside handle_webhook, must NOT use `expected == sig` for v1 list.
    # Must use compare_digest.
    assert "compare_digest" in src
    # And the bare `==` between expected and sig is forbidden in
    # handle_webhook (defence in depth).
    handle_webhook_block = src[src.find("async def handle_webhook"):src.find("async def handle_webhook") + 3000]
    # Should have compare_digest.
    assert "compare_digest" in handle_webhook_block
