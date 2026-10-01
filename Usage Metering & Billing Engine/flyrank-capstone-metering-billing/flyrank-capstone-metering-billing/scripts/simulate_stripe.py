"""Offline equivalent of `stripe trigger`: builds Stripe-shaped events and signs them EXACTLY like
Stripe does (t=<ts>,v1=HMAC_SHA256(secret, "<ts>.<payload>")), then POSTs to /webhooks/stripe.

  python -m scripts.simulate_stripe checkout-completed --tenant-id <id>
  python -m scripts.simulate_stripe checkout-completed --tenant-id <id> --event-id evt_1 --repeat 2   # replay
  python -m scripts.simulate_stripe subscription-updated --tenant-id <id> --status past_due
  python -m scripts.simulate_stripe subscription-deleted --tenant-id <id>
  python -m scripts.simulate_stripe forged --tenant-id <id>       # bad signature -> expect 400
"""
import argparse
import hashlib
import hmac
import json
import os
import time
import uuid

import httpx


def sign_payload(payload: bytes, secret: str, ts: int | None = None) -> str:
    ts = ts or int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def make_event(kind: str, tenant_id: str, event_id: str | None = None, status: str = "active",
               created: int | None = None) -> dict:
    sub_id, cus_id = f"sub_{tenant_id[:8]}", f"cus_{tenant_id[:8]}"
    created = created or int(time.time())
    base = {"id": event_id or f"evt_{uuid.uuid4().hex[:16]}", "object": "event", "created": created}
    if kind == "checkout-completed":
        obj = {"id": "cs_test_" + uuid.uuid4().hex[:12], "object": "checkout.session", "mode": "subscription",
               "payment_status": "paid", "client_reference_id": tenant_id, "customer": cus_id,
               "subscription": sub_id, "metadata": {"tenant_id": tenant_id}}
        return {**base, "type": "checkout.session.completed", "data": {"object": obj}}
    obj = {"id": sub_id, "object": "subscription", "customer": cus_id, "status": status,
           "metadata": {"tenant_id": tenant_id}}
    if kind == "subscription-updated":
        return {**base, "type": "customer.subscription.updated", "data": {"object": obj}}
    if kind == "subscription-deleted":
        return {**base, "type": "customer.subscription.deleted", "data": {"object": {**obj, "status": "canceled"}}}
    raise SystemExit(f"unknown kind {kind}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("kind", choices=["checkout-completed", "subscription-updated", "subscription-deleted", "forged"])
    p.add_argument("--tenant-id", required=True)
    p.add_argument("--status", default="active")
    p.add_argument("--event-id")
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--url", default=os.environ.get("BASE_URL", "http://localhost:8000") + "/webhooks/stripe")
    p.add_argument("--secret", default=os.environ.get("STRIPE_WEBHOOK_SECRET", "whsec_replace_me"))
    a = p.parse_args()
    kind = "checkout-completed" if a.kind == "forged" else a.kind
    event = make_event(kind, a.tenant_id, a.event_id, a.status)
    payload = json.dumps(event).encode()
    secret = "whsec_WRONG_secret" if a.kind == "forged" else a.secret
    for i in range(a.repeat):
        r = httpx.post(a.url, content=payload, headers={"Stripe-Signature": sign_payload(payload, secret),
                                                        "Content-Type": "application/json"})
        print(f"[{i + 1}/{a.repeat}] {event['type']} id={event['id']} -> HTTP {r.status_code} {r.text}")


if __name__ == "__main__":
    main()
