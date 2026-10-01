"""Stripe webhook processing: verified -> deduplicated -> applied, all in ONE transaction.

* Dedupe: stripe_events.id is a PRIMARY KEY; inserting it first means a replayed event hits
  IntegrityError and the whole transaction (including any state change) is discarded.
* Ordering: events older than the last applied one for the tenant's subscription are ignored.
* The DB mirrors Stripe through verified events only.
"""
import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app import clock
from app.db import SessionLocal
from app.models import StripeEvent, Subscription, Tenant

log = logging.getLogger("metering.webhooks")

ACTIVE = {"active", "trialing"}
PAYMENT_PROBLEM = {"past_due": "past_due", "unpaid": "unpaid", "incomplete": "past_due"}
ENDED = {"canceled", "incomplete_expired"}


def process_event(event: dict) -> str:
    """Returns 'processed' | 'duplicate' | 'ignored:<reason>'."""
    with SessionLocal() as s:
        try:
            ev = StripeEvent(id=event["id"], type=event["type"], created=int(event.get("created", 0)),
                             outcome="pending", processed_at=clock.utcnow())
            s.add(ev)
            s.flush()  # PK conflict here => duplicate delivery
        except IntegrityError:
            s.rollback()
            return "duplicate"
        outcome = _apply(s, event)
        ev.outcome = outcome
        s.commit()
        return "processed" if outcome == "applied" else f"ignored:{outcome}"


def _apply(s, event: dict) -> str:
    etype, obj, created = event["type"], event["data"]["object"], int(event.get("created", 0))
    if etype == "checkout.session.completed":
        return _checkout_completed(s, obj, created)
    if etype in ("customer.subscription.updated", "customer.subscription.deleted"):
        return _subscription_changed(s, obj, created, deleted=etype.endswith("deleted"))
    return "unhandled_type"


def _checkout_completed(s, obj, created):
    if obj.get("mode") != "subscription":
        return "not_subscription"
    if obj.get("payment_status") not in ("paid", "no_payment_required"):
        return "not_paid_yet"
    tenant_id = obj.get("client_reference_id") or (obj.get("metadata") or {}).get("tenant_id")
    tenant = s.get(Tenant, tenant_id) if tenant_id else None
    if tenant is None:
        return "unknown_tenant"
    sub = s.scalar(select(Subscription).where(Subscription.tenant_id == tenant.id))
    if sub and created < sub.last_event_created:
        return "stale_event"
    if sub is None:
        sub = Subscription(tenant_id=tenant.id, status="active", last_event_created=0, updated_at=clock.utcnow())
        s.add(sub)
    sub.stripe_customer_id = obj.get("customer")
    sub.stripe_subscription_id = obj.get("subscription")
    sub.status, sub.last_event_created, sub.updated_at = "active", created, clock.utcnow()
    tenant.plan_id, tenant.billing_status = "pro", "active"
    return "applied"


def _subscription_changed(s, obj, created, deleted: bool):
    sub = s.scalar(select(Subscription).where(Subscription.stripe_subscription_id == obj.get("id")))
    if sub is None:
        # event may beat checkout.session.completed: link via metadata only if no subscription is bound yet
        tid = (obj.get("metadata") or {}).get("tenant_id")
        sub = s.scalar(select(Subscription).where(Subscription.tenant_id == tid)) if tid else None
        if sub is not None and sub.stripe_subscription_id not in (None, obj.get("id")):
            return "stale_subscription"
        if sub is None:
            if not tid or s.get(Tenant, tid) is None:
                return "unknown_subscription"
            sub = Subscription(tenant_id=tid, status="incomplete", last_event_created=0, updated_at=clock.utcnow())
            s.add(sub)
        sub.stripe_subscription_id, sub.stripe_customer_id = obj.get("id"), obj.get("customer")
    if created < sub.last_event_created:
        return "stale_event"
    status = "canceled" if deleted else obj.get("status", "")
    tenant = s.get(Tenant, sub.tenant_id)
    sub.status, sub.last_event_created, sub.updated_at = status, created, clock.utcnow()
    if status in ACTIVE:
        tenant.plan_id, tenant.billing_status = "pro", "active"
    elif status in PAYMENT_PROBLEM:
        tenant.plan_id, tenant.billing_status = "pro", PAYMENT_PROBLEM[status]
    elif status in ENDED:
        tenant.plan_id, tenant.billing_status = "free", "active"
    else:
        return "unhandled_status"
    return "applied"
