"""Stripe Checkout (test mode). Payment truth lives at Stripe; we only create sessions here.
The plan flips ONLY when a verified webhook arrives (see webhooks.py)."""
import logging

import stripe
from sqlalchemy import select

from app.config import get_settings
from app.errors import ApiError
from app.models import Subscription, Tenant

log = logging.getLogger("metering.billing")


def create_checkout_session(session, tenant: Tenant, plan_id: str) -> dict:
    s = get_settings()
    if plan_id != "pro":
        raise ApiError(422, "unsupported_plan", "Only an upgrade to 'pro' is available via checkout.")
    if tenant.plan_id == "pro" and tenant.billing_status == "active":
        raise ApiError(409, "already_subscribed", "This tenant is already on the Pro plan.")

    if s.stripe_mock:
        return {"mode": "mock", "session_id": f"cs_mock_{tenant.id[:8]}", "checkout_url": None,
                "tenant_id": tenant.id,
                "message": "STRIPE_SECRET_KEY is not set: offline mock mode. Simulate payment with "
                           f"`python -m scripts.simulate_stripe checkout-completed --tenant-id {tenant.id}`."}

    if not s.stripe_secret_key.startswith("sk_test_"):
        raise ApiError(500, "stripe_misconfigured", "Only Stripe TEST keys (sk_test_...) are allowed.")
    if not s.stripe_price_id_pro:
        raise ApiError(503, "stripe_misconfigured", "STRIPE_PRICE_ID_PRO is not configured.")
    sub = session.scalar(select(Subscription).where(Subscription.tenant_id == tenant.id))
    params = dict(
        mode="subscription",
        line_items=[{"price": s.stripe_price_id_pro, "quantity": 1}],
        client_reference_id=tenant.id,
        metadata={"tenant_id": tenant.id},
        subscription_data={"metadata": {"tenant_id": tenant.id}},
        success_url=s.checkout_success_url,
        cancel_url=s.checkout_cancel_url,
    )
    if sub and sub.stripe_customer_id:
        params["customer"] = sub.stripe_customer_id
    try:
        cs = stripe.checkout.Session.create(api_key=s.stripe_secret_key, **params)
    except stripe.StripeError as exc:
        log.error("stripe checkout failed: %s", type(exc).__name__)
        raise ApiError(502, "stripe_error", "Could not create a Stripe Checkout session.")
    return {"mode": "stripe_test", "session_id": cs.id, "checkout_url": cs.url, "tenant_id": tenant.id}
