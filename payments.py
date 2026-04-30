"""
Huntova SaaS — Payments (Stripe)
Checkout sessions, webhooks, credit top-ups, subscriptions.
"""
import os
import json
import requests as _req
from datetime import datetime, timezone, timedelta

import db
from config import PUBLIC_URL

STRIPE_SECRET = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_API = "https://api.stripe.com/v1"
BASE_URL = PUBLIC_URL

# ── Products ──
PRODUCTS = {
    "growth_monthly": {
        "name": "Growth Plan",
        "description": "25 leads/month, AI chat, full contacts",
        "price_cents": 4900,
        "currency": "eur",
        "credits": 25,
        "tier": "growth",
        "mode": "subscription",
        "interval": "month",
    },
    "agency_monthly": {
        "name": "Agency Plan",
        "description": "50 leads/month, Gemini Pro AI",
        "price_cents": 14900,
        "currency": "eur",
        "credits": 50,
        "tier": "agency",
        "mode": "subscription",
        "interval": "month",
    },
    "topup_10": {
        "name": "10 Lead Credits",
        "description": "10 lead credits — never expire",
        "price_cents": 1900,
        "currency": "eur",
        "credits": 10,
        "tier": None,
        "mode": "payment",
    },
    "topup_30": {
        "name": "30 Lead Credits",
        "description": "30 lead credits — never expire",
        "price_cents": 4900,
        "currency": "eur",
        "credits": 30,
        "tier": None,
        "mode": "payment",
    },
    "topup_75": {
        "name": "75 Lead Credits",
        "description": "75 lead credits — never expire",
        "price_cents": 9900,
        "currency": "eur",
        "credits": 75,
        "tier": None,
        "mode": "payment",
    },
}


def _stripe(method, endpoint, **kwargs):
    if not STRIPE_SECRET:
        raise RuntimeError("Stripe not configured")
    url = f"{STRIPE_API}/{endpoint}"
    headers = {"Authorization": f"Bearer {STRIPE_SECRET}"}
    if method == "GET":
        r = _req.get(url, headers=headers, params=kwargs, timeout=15)
    else:
        r = _req.post(url, headers=headers, data=kwargs, timeout=15)
    r.raise_for_status()
    return r.json()


async def create_checkout(user_id: int, product_id: str) -> dict:
    product = PRODUCTS.get(product_id)
    if not product:
        raise ValueError(f"Unknown product: {product_id}")

    user = await db.get_user_by_id(user_id)
    if not user:
        raise ValueError("User not found")

    params = {
        "mode": product["mode"],
        "success_url": f"{BASE_URL}/?payment=success",
        "cancel_url": f"{BASE_URL}/?payment=cancelled",
        "customer_email": user["email"],
        "metadata[user_id]": str(user_id),
        "metadata[product_id]": product_id,
        "line_items[0][price_data][currency]": product.get("currency", "eur"),
        "line_items[0][price_data][product_data][name]": product["name"],
        "line_items[0][price_data][product_data][description]": product["description"],
        "line_items[0][price_data][unit_amount]": str(product["price_cents"]),
        "line_items[0][quantity]": "1",
    }

    if product["mode"] == "subscription":
        params["line_items[0][price_data][recurring][interval]"] = product.get("interval", "month")
        # Stability fix (Perplexity bug #75): Stripe does NOT auto-copy
        # session metadata onto the Subscription object that gets
        # created. Renewal/update/cancel webhooks need the user_id +
        # product_id on the subscription itself; without
        # subscription_data[metadata] they fall back to fragile email
        # lookup (case mismatch, deleted users, races). Set both so
        # every subscription-bound event can resolve the user
        # directly.
        params["subscription_data[metadata][user_id]"] = str(user_id)
        params["subscription_data[metadata][product_id]"] = product_id

    import asyncio
    result = await asyncio.to_thread(_stripe, "POST", "checkout/sessions", **params)
    if not result.get("url"):
        raise RuntimeError("Stripe did not return a checkout URL")
    return {"url": result["url"], "session_id": result.get("id", "")}


async def handle_webhook(payload: bytes, sig_header: str) -> dict:
    """Process Stripe webhook. Validates signature — rejects all unsigned requests."""
    import hmac, hashlib, time as _time

    # Reject if webhook secret not configured
    if not STRIPE_WEBHOOK_SECRET:
        return {"ok": False, "error": "Webhook secret not configured — rejecting unsigned request"}

    # Reject if no signature header provided
    if not sig_header:
        return {"ok": False, "error": "Missing Stripe-Signature header"}

    # Validate signature
    try:
        # Parse Stripe signature header: t=timestamp,v1=sig1,v1=sig2 (multiple v1 during key rotation)
        _pairs = [p.split("=", 1) for p in sig_header.split(",") if "=" in p]
        timestamp = next((v for k, v in _pairs if k == "t"), "")
        v1_sigs = [v for k, v in _pairs if k == "v1"]
        if not timestamp or not v1_sigs:
            return {"ok": False, "error": "Missing signature components"}
        # Verify against ALL v1 signatures (Stripe sends multiple during key rotation)
        signed_payload = f"{timestamp}.{payload.decode('utf-8')}"
        expected = hmac.HMAC(STRIPE_WEBHOOK_SECRET.encode(), signed_payload.encode(), hashlib.sha256).hexdigest()
        if not any(hmac.compare_digest(expected, sig) for sig in v1_sigs):
            return {"ok": False, "error": "Invalid signature"}
        # Reject old events (>5 min tolerance)
        if abs(_time.time() - int(timestamp)) > 300:
            return {"ok": False, "error": "Timestamp too old"}
    except Exception:
        return {"ok": False, "error": "Signature validation failed"}

    try:
        event = json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        return {"ok": False, "error": "Invalid JSON payload"}
    event_type = event.get("type", "")

    event_id = event.get("id", "")

    # Stability fix (multi-agent bug #25): every Stripe event has a non-
    # empty id; if we somehow receive one without it, idempotency falls
    # off (the existing `if event_id:` gates below would skip the
    # record_webhook write and the same event could process twice on
    # replay). Reject upfront — better to bounce a malformed payload
    # than process it ambiguously.
    if not event_id:
        return {"ok": False, "error": "Webhook event missing id"}

    # Idempotency: check if we've already processed this event
    already = await db.check_webhook_processed(event_id)
    if already:
        return {"ok": True, "message": "Already processed"}

    # Stability fix (Perplexity bug #52): record_webhook below claims
    # the event_id BEFORE the side-effect writes (credit grant, tier
    # change, refund log) finish. If anything between the claim and
    # the final ledger write raises, Stripe will retry — but the
    # claim row is still there, so the retry short-circuits and the
    # user permanently misses credits. Wrap the whole dispatch so any
    # raised exception rolls back the claim before propagating; Stripe
    # gets 5xx, retries, and the next attempt sees a clean state.
    try:
        return await _dispatch_webhook_event(event, event_type, event_id)
    except Exception:
        try:
            await db.rollback_webhook(event_id)
        except Exception as _rb_err:
            print(f"[STRIPE webhook] rollback failed for {event_id}: {_rb_err}")
        raise


async def _dispatch_webhook_event(event: dict, event_type: str, event_id: str) -> dict:
    if event_type in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        session = event.get("data", {}).get("object", {})
        metadata = session.get("metadata", {})
        user_id = int(metadata.get("user_id", 0))
        product_id = metadata.get("product_id", "")

        if not user_id or not product_id:
            return {"ok": False, "error": "Missing metadata"}

        product = PRODUCTS.get(product_id)
        if not product:
            return {"ok": False, "error": "Unknown product"}

        # Stability fix (Perplexity bug #47): Stripe fires BOTH
        # checkout.session.completed AND
        # checkout.session.async_payment_succeeded for async payment
        # methods (SEPA, Bacs, etc.) — completed fires when the user
        # finishes checkout but the payment hasn't settled yet
        # (payment_status="unpaid"); async_payment_succeeded fires when
        # the delayed payment actually clears. Each has its own
        # event_id so record_webhook can't dedupe them. Without this
        # gate the user gets credited twice.
        # For sync (card) payments the completed event arrives with
        # payment_status="paid" and the async event never fires, so
        # this branch is a no-op for normal cases.
        if event_type == "checkout.session.completed" and session.get("payment_status") != "paid":
            return {"ok": True, "message": "Checkout completed; awaiting async payment confirmation"}

        # Record webhook FIRST to prevent double-processing from concurrent requests.
        # record_webhook returns False when the row already existed (ON CONFLICT),
        # so a second concurrent webhook for the same event short-circuits here
        # instead of double-crediting.
        if event_id:
            first_writer = await db.record_webhook(event_id, event_type, user_id, product_id)
            if not first_writer:
                return {"ok": True, "message": "Already processed (race-caught)"}

        user = await db.get_user_by_id(user_id)
        if not user:
            return {"ok": True, "message": "User no longer exists — payment recorded"}

        # Stability fix (multi-agent bug #26): atomic credit increment.
        # Previously this was read-then-write on credits_remaining; a
        # concurrent second-checkout webhook or a same-instant /auth/me
        # monthly refill could overwrite the freshly-added credits with
        # a stale snapshot. Now incremented via SQL in-place. Tier +
        # reset_date are still set absolutely (intended overwrites for
        # a paid subscription).
        await db._aexec(
            "UPDATE users SET credits_remaining = credits_remaining + %s WHERE id = %s",
            [product["credits"], user_id])

        if product.get("tier"):
            tier_update = {
                "tier": product["tier"],
                "credits_reset_date": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
            }
            await db.update_user(user_id, **tier_update)

        # Read post-write balance for the ledger entry.
        fresh = await db.get_user_by_id(user_id)
        new_credits = (fresh or {}).get("credits_remaining", 0) or 0

        # Log to credit ledger
        await db.add_credit_ledger(user_id, product["credits"], new_credits,
            "topup" if not product.get("tier") else "subscription",
            product_id)

        return {"ok": True, "user_id": user_id, "credits_added": product["credits"],
                "new_tier": product.get("tier"), "total_credits": new_credits}

    # Handle subscription renewal payments (month 2+).
    # Stability fix (multi-agent bug #16): we used to refill on EVERY
    # invoice.paid that had a subscription + amount_paid > 0. Stripe also
    # fires invoice.paid for the FIRST invoice of a new subscription
    # (billing_reason="subscription_create"), so the new subscriber got
    # the tier credits twice — once from checkout.session.completed and
    # again from this branch. Now gated to genuine renewals only.
    if event_type == "invoice.paid":
        invoice = event.get("data", {}).get("object", {})
        sub_id = invoice.get("subscription", "")
        customer_email = invoice.get("customer_email", "")
        amount_paid = invoice.get("amount_paid", 0)
        billing_reason = invoice.get("billing_reason", "")
        # Skip the first-month invoice — its credits were already added by
        # checkout.session.completed above.
        if billing_reason == "subscription_create":
            return {"ok": True, "message": "First-month invoice — credits already added by checkout"}
        # Only process subscription invoices (not one-time)
        if sub_id and amount_paid > 0:
            # Find user: try subscription metadata first, then email
            user = None
            sub_meta = invoice.get("subscription_details", {}).get("metadata", {})
            if sub_meta.get("user_id"):
                try:
                    user = await db.get_user_by_id(int(sub_meta["user_id"]))
                except (ValueError, TypeError):
                    pass
            if not user and customer_email:
                user = await db.get_user_by_email(customer_email)
            if user:
                tier = user.get("tier", "free")
                from config import TIERS
                tier_info = TIERS.get(tier, TIERS.get("free", {}))
                refill = tier_info.get("credits", 0)
                if refill > 0:
                    # Record first — same race-safety pattern as checkout above.
                    if event_id:
                        first_writer = await db.record_webhook(event_id, event_type, user["id"], f"renewal:{tier}")
                        if not first_writer:
                            return {"ok": True, "message": "Already processed (race-caught)"}
                    # Stability fix (multi-agent bug #35, sibling of #26):
                    # the previous version did read-then-write on
                    # credits_remaining. Two concurrent invoice.paid
                    # events (or one webhook racing the /auth/me refill)
                    # could read the same stale snapshot and one write
                    # would overwrite the other. Now atomic in-place
                    # increment for credits_remaining; reset_date stays
                    # absolute since it's an intended overwrite.
                    new_reset = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
                    await db._aexec(
                        "UPDATE users SET credits_remaining = credits_remaining + %s, credits_reset_date = %s WHERE id = %s",
                        [refill, new_reset, user["id"]])
                    fresh = await db.get_user_by_id(user["id"])
                    new_credits = (fresh or {}).get("credits_remaining", 0) or 0
                    await db.add_credit_ledger(user["id"], refill, new_credits, "subscription_renewal", f"tier:{tier}")
                    return {"ok": True, "user_id": user["id"], "credits_added": refill, "renewal": True}
        return {"ok": True, "message": "Invoice processed (no action needed)"}

    # Handle subscription cancellation
    if event_type == "customer.subscription.deleted":
        sub = event.get("data", {}).get("object", {})
        metadata = sub.get("metadata", {})
        # Primary: find user by user_id from metadata (set during checkout)
        # Fallback: find by customer_email (available on some API versions)
        user = None
        if metadata.get("user_id"):
            try:
                user = await db.get_user_by_id(int(metadata["user_id"]))
            except (ValueError, TypeError):
                pass
        if not user:
            # Fallback: try customer_email from subscription or invoice
            customer_email = sub.get("customer_email", "") or metadata.get("email", "")
            if customer_email:
                user = await db.get_user_by_email(customer_email)
        if user and user.get("tier") != "free":
            # Record first — race-safety, as above.
            if event_id:
                first_writer = await db.record_webhook(event_id, event_type, user["id"], "cancelled")
                if not first_writer:
                    return {"ok": True, "message": "Already processed (race-caught)"}
            # Stability fix (Perplexity bug #48): the previous version
            # clamped credits_remaining to the free tier ceiling on
            # cancel. That burned ANY one-time topup credits the user
            # had paid for separately — credits are fungible in
            # credits_remaining (no per-source bucket), so there's no
            # safe way to tell what was subscription vs topup. Don't
            # clamp; just downgrade tier + clear reset_date. The user
            # keeps whatever credits they had left and can spend them
            # on the free tier. Cleaner accounting (no mystery
            # disappearance) and respects what they paid for.
            current_credits = user.get("credits_remaining", 0) or 0
            await db.update_user(user["id"], tier="free", credits_reset_date="")
            await db.add_credit_ledger(user["id"], 0, current_credits, "subscription_cancelled", "")
            return {"ok": True, "user_id": user["id"], "tier": "free", "credits": current_credits}

    # Stability fix (multi-agent bug #30): refunds were entirely silent —
    # the user kept their credits + tier, we kept their money minus
    # Stripe's debit. We can't reliably reverse the credit grant without
    # storing the original PaymentIntent linkage, but at minimum we log
    # the refund and email admins so it doesn't go unnoticed.
    if event_type == "charge.refunded":
        charge = event.get("data", {}).get("object", {})
        amount_refunded = charge.get("amount_refunded", 0)
        currency = charge.get("currency", "")
        receipt_email = charge.get("receipt_email") or charge.get("billing_details", {}).get("email", "")
        charge_id = charge.get("id", "")
        # Try to find the user from charge metadata (Stripe propagates
        # checkout-session metadata onto the underlying PaymentIntent and
        # then the Charge in most cases).
        meta = charge.get("metadata", {}) or {}
        user_id_meta = meta.get("user_id")
        target_user = None
        if user_id_meta:
            try:
                target_user = await db.get_user_by_id(int(user_id_meta))
            except (ValueError, TypeError):
                target_user = None
        if not target_user and receipt_email:
            target_user = await db.get_user_by_email(receipt_email.lower())
        # Record-on-conflict so retried Stripe deliveries don't double-log.
        first_writer = await db.record_webhook(event_id, event_type, target_user["id"] if target_user else 0, f"refund:{charge_id}")
        if not first_writer:
            return {"ok": True, "message": "Already processed (race-caught)"}
        # Ledger entry — 0 credit delta, message carries the receipt.
        if target_user:
            try:
                cur = (target_user.get("credits_remaining") or 0)
                await db.add_credit_ledger(
                    target_user["id"], 0, cur, "refund_received",
                    f"charge:{charge_id} amount:{amount_refunded} {currency}")
            except Exception as _le:
                print(f"[STRIPE refund] ledger write failed: {_le}")
        # Alert admins. Email failure is best-effort — already-recorded
        # webhook means we won't double-alert on Stripe retry.
        try:
            from config import ADMIN_EMAILS
            import email_service
            if ADMIN_EMAILS and email_service.is_email_configured():
                _subj = f"[Huntova] Stripe refund {charge_id}"
                _body = (f"Stripe refunded {amount_refunded} {currency} on charge {charge_id}.\n\n"
                         f"User: {target_user.get('email') if target_user else (receipt_email or 'unknown')}\n"
                         f"User ID: {target_user.get('id') if target_user else 'not found'}\n\n"
                         "ACTION: review credits + tier manually. Auto-deduction was not applied.")
                for _admin in ADMIN_EMAILS[:3]:
                    try:
                        await email_service.send_email(_admin, _subj, f"<pre>{_body}</pre>", _body)
                    except Exception:
                        pass
        except Exception as _alert_err:
            print(f"[STRIPE refund] admin alert failed: {_alert_err}")
        return {"ok": True, "refund_logged": True, "user_id": target_user["id"] if target_user else None}

    # Stability fix (multi-agent bug #31): plan changes via Stripe portal
    # used to be silent — Huntova kept the user's old tier and old
    # credit allowance even after they upgraded/downgraded. Now we map
    # the Stripe price/product back to our PRODUCTS dict and align tier.
    if event_type == "customer.subscription.updated":
        sub = event.get("data", {}).get("object", {})
        sub_meta = sub.get("metadata", {}) or {}
        # Locate user
        user = None
        if sub_meta.get("user_id"):
            try:
                user = await db.get_user_by_id(int(sub_meta["user_id"]))
            except (ValueError, TypeError):
                pass
        if not user:
            cust_email = sub.get("customer_email") or ""
            if cust_email:
                user = await db.get_user_by_email(cust_email.lower())
        if not user:
            return {"ok": True, "message": "subscription.updated — user not found"}
        # Stability fix (Perplexity bug #61): scan ALL subscription
        # items, not just items[0]. Stripe subscriptions can contain
        # multiple items (e.g. base plan + add-ons), and we don't
        # control the order. Reading only the first one would miss
        # the plan-defining item or pick an add-on by mistake.
        items = (sub.get("items") or {}).get("data") or []
        new_tier = None
        for _item in items:
            _price = _item.get("price") or {}
            _unit_amount = _price.get("unit_amount")
            for _pid, _p in PRODUCTS.items():
                if _p.get("mode") == "subscription" and _p.get("price_cents") == _unit_amount:
                    new_tier = _p.get("tier")
                    break
            if new_tier:
                break
        if not new_tier or new_tier == user.get("tier"):
            return {"ok": True, "message": "subscription.updated — no tier change"}
        # Idempotency before write.
        first_writer = await db.record_webhook(event_id, event_type, user["id"], f"plan_change:{new_tier}")
        if not first_writer:
            return {"ok": True, "message": "Already processed (race-caught)"}
        await db.update_user(user["id"], tier=new_tier)
        await db.add_credit_ledger(
            user["id"], 0, user.get("credits_remaining", 0) or 0,
            "subscription_plan_changed", f"to:{new_tier}")
        return {"ok": True, "user_id": user["id"], "new_tier": new_tier}

    return {"ok": True, "ignored": event_type}


def is_stripe_configured() -> bool:
    return bool(STRIPE_SECRET)
