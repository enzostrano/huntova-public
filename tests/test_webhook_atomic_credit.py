"""Regression test for BRAIN-45 (a406): Stripe webhook used
two-connection pattern for credit grants. Atomic UPDATE on
credits_remaining then separate add_credit_ledger.

If ledger insert fails, exception bubbles to handle_webhook
which rollback_webhook deletes the stripe_events claim row,
but credits were already incremented. Stripe retries, claim
succeeds again, credits incremented twice. User gets double.

Fix: use db.apply_credit_delta (atomic UPDATE + ledger in one
transaction).

Per GPT-5.4 audit on Stripe webhook idempotency.
"""
from __future__ import annotations
import inspect


def test_checkout_branch_uses_atomic_credit_helper():
    import payments
    src = inspect.getsource(payments._dispatch_webhook_event)
    assert "apply_credit_delta" in src, (
        "BRAIN-45: checkout.session.completed branch must call "
        "apply_credit_delta. The UPDATE + add_credit_ledger split "
        "across two connections is double-credit prone on rollback."
    )


def test_both_credit_grant_branches_use_atomic_helper():
    import payments
    src = inspect.getsource(payments._dispatch_webhook_event)
    assert src.count("apply_credit_delta") >= 2, (
        "BRAIN-45: both checkout and invoice.paid renewal branches "
        "must use apply_credit_delta. Found "
        f"{src.count('apply_credit_delta')} usage(s); expected >= 2."
    )
