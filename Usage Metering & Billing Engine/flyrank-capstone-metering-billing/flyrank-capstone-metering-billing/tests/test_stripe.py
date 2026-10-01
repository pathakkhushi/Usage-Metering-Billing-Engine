import json
import time

from scripts.simulate_stripe import make_event, sign_payload
from tests.conftest import gen, make_tenant

SECRET = "whsec_test_secret"


def post(client, event, secret=SECRET, ts=None):
    payload = json.dumps(event).encode()
    return client.post("/webhooks/stripe", content=payload,
                       headers={"Stripe-Signature": sign_payload(payload, secret, ts), "Content-Type": "application/json"})


def plan_of(client, h):
    return client.get("/usage", headers=h).json()


def test_checkout_mock_mode_returns_instructions(client):
    tid, h = make_tenant(client)
    r = client.post("/billing/checkout", json={"plan": "pro"}, headers=h)
    assert r.status_code == 200 and r.json()["mode"] == "mock" and tid in r.json()["message"]
    assert client.post("/billing/checkout", json={"plan": "gold"}, headers=h).status_code == 422


def test_checkout_completed_flips_free_to_pro_and_usage_shows_new_limits(client):
    tid, h = make_tenant(client)
    assert plan_of(client, h)["usage"]["api_calls"]["limit"] == 1000
    r = post(client, make_event("checkout-completed", tid))
    assert r.status_code == 200 and r.json()["outcome"] == "processed"
    d = plan_of(client, h)
    assert d["plan"]["id"] == "pro" and d["usage"]["api_calls"]["limit"] == 50_000
    assert d["usage"]["ai_tokens"]["limit"] == 5_000_000 and d["cost"]["amount_due_micro_usd"] == 20_000_000
    assert client.post("/billing/checkout", json={"plan": "pro"}, headers=h).status_code == 409


def test_forged_signature_returns_400_and_changes_nothing(client):
    tid, h = make_tenant(client)
    assert post(client, make_event("checkout-completed", tid), secret="whsec_attacker").status_code == 400
    assert client.post("/webhooks/stripe", content=b"{}").status_code == 400            # no header
    ev = make_event("checkout-completed", tid)
    payload = json.dumps(ev).encode()
    sig = sign_payload(payload, SECRET)
    tampered = payload.replace(b"paid", b"PAID")                                          # body altered after signing
    assert client.post("/webhooks/stripe", content=tampered, headers={"Stripe-Signature": sig}).status_code == 400
    assert post(client, ev, ts=int(time.time()) - 3600).status_code == 400               # stale timestamp (replay attack)
    assert plan_of(client, h)["plan"]["id"] == "free"


def test_duplicate_event_is_processed_once(client):
    tid, h = make_tenant(client)
    ev = make_event("checkout-completed", tid, event_id="evt_dup")
    assert post(client, ev).json()["outcome"] == "processed"
    assert post(client, ev).json()["outcome"] == "duplicate"
    # prove the replay really is ignored: downgrade, then replay again -> must NOT re-upgrade
    post(client, make_event("subscription-deleted", tid, created=ev["created"] + 10))
    assert plan_of(client, h)["plan"]["id"] == "free"
    assert post(client, ev).json()["outcome"] == "duplicate"
    assert plan_of(client, h)["plan"]["id"] == "free"


def test_subscription_lifecycle_past_due_then_recovered_then_deleted(client):
    tid, h = make_tenant(client)
    t0 = int(time.time())
    post(client, make_event("checkout-completed", tid, created=t0))
    post(client, make_event("subscription-updated", tid, status="past_due", created=t0 + 1))
    d = plan_of(client, h)
    assert d["plan"]["id"] == "pro" and d["billing_status"] == "past_due"
    assert gen(client, h, "k").status_code == 402
    post(client, make_event("subscription-updated", tid, status="active", created=t0 + 2))
    assert gen(client, h, "k").status_code == 200
    post(client, make_event("subscription-deleted", tid, created=t0 + 3))
    d = plan_of(client, h)
    assert d["plan"]["id"] == "free" and d["billing_status"] == "active"


def test_out_of_order_events_do_not_regress_state(client):
    tid, h = make_tenant(client)
    t0 = int(time.time())
    post(client, make_event("checkout-completed", tid, created=t0))
    post(client, make_event("subscription-deleted", tid, created=t0 + 10))
    r = post(client, make_event("subscription-updated", tid, status="active", created=t0 + 5))  # older, arrives late
    assert r.json()["outcome"] == "ignored:stale_event"
    assert plan_of(client, h)["plan"]["id"] == "free"


def test_unknown_tenant_and_unhandled_types_are_acked_not_errors(client):
    assert post(client, make_event("checkout-completed", "no-such-tenant")).json()["outcome"] == "ignored:unknown_tenant"
    ev = make_event("checkout-completed", "x")
    ev["type"] = "invoice.created"
    assert post(client, ev).json()["outcome"] == "ignored:unhandled_type"
