"""BRAIN-207: payments.PRODUCTS catalog invariant audit.

The `PRODUCTS` dict is the source-of-truth for pricing. Stripe
checkout sessions, subscription renewals, and lead-credit topups
all read from here. A regression in price_cents or credits silently
mis-bills the user.

Pinned invariants:

1. `PRODUCTS` is a non-empty dict.
2. Each product has the required keys (name, description,
   price_cents, currency, credits, mode).
3. `price_cents` is positive int.
4. `credits` is positive int.
5. `currency` is a 3-char ISO code.
6. `mode` is `"subscription"` or `"payment"`.
7. Subscription products have `interval` set.
8. Topup products are payment-mode (one-off, not subscription).
9. Tier-bundled products (growth, agency) have `tier` set.
10. Topup products have `tier=None` (credits don't change tier).
"""
from __future__ import annotations


_REQUIRED_KEYS = {"name", "description", "price_cents", "currency",
                  "credits", "mode"}


def test_products_is_non_empty_dict():
    from payments import PRODUCTS
    assert isinstance(PRODUCTS, dict)
    assert len(PRODUCTS) > 0


def test_each_product_has_required_keys():
    from payments import PRODUCTS
    for slug, prod in PRODUCTS.items():
        missing = _REQUIRED_KEYS - set(prod.keys())
        assert not missing, f"product {slug!r} missing keys: {missing}"


def test_price_cents_positive_int():
    from payments import PRODUCTS
    for slug, prod in PRODUCTS.items():
        assert isinstance(prod["price_cents"], int), (
            f"product {slug!r} price_cents must be int, got {type(prod['price_cents']).__name__}"
        )
        assert prod["price_cents"] > 0, f"product {slug!r} has non-positive price"


def test_credits_positive_int():
    from payments import PRODUCTS
    for slug, prod in PRODUCTS.items():
        assert isinstance(prod["credits"], int)
        assert prod["credits"] > 0, f"product {slug!r} has non-positive credits"


def test_currency_iso_3char():
    from payments import PRODUCTS
    for slug, prod in PRODUCTS.items():
        c = prod["currency"]
        assert isinstance(c, str)
        assert len(c) == 3, f"product {slug!r} currency {c!r} must be 3-char ISO"
        # Lowercase per Stripe convention.
        assert c == c.lower()


def test_mode_is_subscription_or_payment():
    from payments import PRODUCTS
    for slug, prod in PRODUCTS.items():
        assert prod["mode"] in ("subscription", "payment"), (
            f"product {slug!r} mode {prod['mode']!r} not valid"
        )


def test_subscription_products_have_interval():
    """Subscriptions need a billing interval (month / year)."""
    from payments import PRODUCTS
    for slug, prod in PRODUCTS.items():
        if prod["mode"] == "subscription":
            assert "interval" in prod, (
                f"subscription product {slug!r} missing interval"
            )
            assert prod["interval"] in ("month", "year"), (
                f"product {slug!r} interval {prod.get('interval')!r} not valid"
            )


def test_growth_plan_exists():
    from payments import PRODUCTS
    assert "growth_monthly" in PRODUCTS
    assert PRODUCTS["growth_monthly"]["mode"] == "subscription"
    assert PRODUCTS["growth_monthly"]["tier"] == "growth"


def test_agency_plan_exists():
    from payments import PRODUCTS
    assert "agency_monthly" in PRODUCTS
    assert PRODUCTS["agency_monthly"]["mode"] == "subscription"
    assert PRODUCTS["agency_monthly"]["tier"] == "agency"


def test_topup_products_payment_mode():
    """Topups are one-off payments, not subscriptions."""
    from payments import PRODUCTS
    for slug, prod in PRODUCTS.items():
        if slug.startswith("topup_"):
            assert prod["mode"] == "payment"
            assert prod["tier"] is None  # credits don't change tier


def test_topup_credits_match_slug():
    """`topup_10` has 10 credits, `topup_30` has 30, `topup_75` has 75."""
    from payments import PRODUCTS
    if "topup_10" in PRODUCTS:
        assert PRODUCTS["topup_10"]["credits"] == 10
    if "topup_30" in PRODUCTS:
        assert PRODUCTS["topup_30"]["credits"] == 30
    if "topup_75" in PRODUCTS:
        assert PRODUCTS["topup_75"]["credits"] == 75


def test_no_zero_or_negative_pricing():
    """Defensive: no product can be free or negative-priced."""
    from payments import PRODUCTS
    for slug, prod in PRODUCTS.items():
        assert prod["price_cents"] >= 100, (
            f"product {slug!r} price {prod['price_cents']} cents is suspiciously low"
        )


def test_currency_consistency():
    """All products use the same currency (no mixed EUR/USD/GBP)."""
    from payments import PRODUCTS
    currencies = {prod["currency"] for prod in PRODUCTS.values()}
    assert len(currencies) == 1, (
        f"PRODUCTS catalog has mixed currencies: {currencies}"
    )


def test_product_descriptions_non_empty():
    """Every product has a non-empty description (shown in Stripe checkout)."""
    from payments import PRODUCTS
    for slug, prod in PRODUCTS.items():
        assert prod["description"], f"product {slug!r} has empty description"


def test_product_names_non_empty():
    from payments import PRODUCTS
    for slug, prod in PRODUCTS.items():
        assert prod["name"], f"product {slug!r} has empty name"
