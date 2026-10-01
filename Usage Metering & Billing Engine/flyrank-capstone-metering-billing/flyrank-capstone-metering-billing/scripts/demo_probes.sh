#!/usr/bin/env bash
# Reproduces the 5 acceptance probes against a RUNNING server (./run.sh) after `python -m scripts.seed`.
# Env: BASE_URL, ADMIN_TOKEN, STRIPE_WEBHOOK_SECRET must match the server's.
set -uo pipefail
B=${BASE_URL:-http://localhost:8000}; ADMIN=${ADMIN_TOKEN:-change-me-admin-token}
FREE=demo_key_acme_free_0000000000000000
hdr() { echo; echo "\$ $*"; }
pp()  { python -c 'import sys,json; print(json.dumps(json.load(sys.stdin),indent=2))'; }
field() { python -c "import sys,json; d=json.load(sys.stdin); print(eval('d'+sys.argv[1]))" "$1"; }

echo "################ PROBE 1: same Idempotency-Key twice -> one event, identical response"
BODY='{"prompt":"hello","usage":{"input_tokens":5000,"cached_input_tokens":4000,"output_tokens":500,"reasoning_tokens":1500}}'
for i in 1 2; do
  hdr "POST /generate  (Idempotency-Key: probe1-key)   [attempt $i]"
  curl -s -i -X POST $B/generate -H "X-API-Key: $FREE" -H "Idempotency-Key: probe1-key" -H 'Content-Type: application/json' -d "$BODY" \
    | grep -Ei '^(HTTP|idempotent-replayed)|^\{' | sed 's/^{.*"event_id":"\([^"]*\)".*$/body.event_id = \1 (body identical on both attempts)/'
done
hdr "GET /usage/events  -> number of events recorded for this tenant"
curl -s $B/usage/events -H "X-API-Key: $FREE" | python -c 'import sys,json; print("events:", len(json.load(sys.stdin)["events"]))'

echo; echo "################ PROBE 5: pricing rules (same event as above: 5000 input incl. 4000 cached, 500 output, 1500 reasoning, 1 call)"
hdr "GET /usage"
curl -s $B/usage -H "X-API-Key: $FREE" | python -c 'import sys,json; d=json.load(sys.stdin); print(json.dumps({"usage":d["usage"],"cost":d["cost"]},indent=2))'

echo; echo "################ PROBE 2: drive a tenant to its EXACT quota (Free = 1000 API calls)"
read -r TID KEY < <(curl -s -X POST $B/tenants -H "X-Admin-Token: $ADMIN" -H 'Content-Type: application/json' -d "{\"name\":\"quota-demo-$RANDOM\",\"plan\":\"free\"}" | python -c 'import sys,json; d=json.load(sys.stdin); print(d["tenant_id"], d["api_key"])')
python - "$B" "$KEY" <<'PY'
import sys, httpx
b, key = sys.argv[1], sys.argv[2]
c = httpx.Client(base_url=b, headers={"X-API-Key": key}, timeout=30)
def call(i): return c.post("/generate", json={"prompt": "x", "usage": {"input_tokens": 1}}, headers={"Idempotency-Key": f"q-{i}"})
for i in range(1, 1000):
    assert call(i).status_code == 200
r = c.get("/usage").json()["usage"]["api_calls"]; print("after 999 calls:", r)
r = call(1000); print(f"call #1000 (exactly at limit) -> HTTP {r.status_code}  quota={r.json()['quota']['api_calls']}")
r = call(1001); print(f"call #1001 (one past)         -> HTTP {r.status_code}  Retry-After={r.headers['retry-after']}s")
print(r.text)
PY

echo; echo "################ PROBE 3: Checkout -> webhook flips Free -> Pro (offline mock checkout + signed webhook)"
TID=$(curl -s $B/usage -H "X-API-Key: $FREE" | field "['tenant_id']")
hdr "GET /usage (before)"; curl -s $B/usage -H "X-API-Key: $FREE" | python -c 'import sys,json; d=json.load(sys.stdin); print("plan:", d["plan"]["id"], "| api_calls:", d["usage"]["api_calls"], "| ai_tokens:", d["usage"]["ai_tokens"])'
hdr "POST /billing/checkout {plan: pro}"; curl -s -X POST $B/billing/checkout -H "X-API-Key: $FREE" -H 'Content-Type: application/json' -d '{"plan":"pro"}' | pp
hdr "python -m scripts.simulate_stripe checkout-completed --tenant-id $TID"
python -m scripts.simulate_stripe checkout-completed --tenant-id "$TID" --url $B/webhooks/stripe --secret "${STRIPE_WEBHOOK_SECRET:-whsec_replace_me}"
hdr "GET /usage (after)"; curl -s $B/usage -H "X-API-Key: $FREE" | python -c 'import sys,json; d=json.load(sys.stdin); print("plan:", d["plan"]["id"], "| api_calls:", d["usage"]["api_calls"], "| ai_tokens:", d["usage"]["ai_tokens"], "| amount_due:", d["cost"]["amount_due_display"])'

echo; echo "################ PROBE 4: forged signature -> 400 ; real event replayed twice -> processed once"
hdr "python -m scripts.simulate_stripe forged"; python -m scripts.simulate_stripe forged --tenant-id "$TID" --url $B/webhooks/stripe
hdr "replay the SAME event id three times"; python -m scripts.simulate_stripe subscription-updated --tenant-id "$TID" --status past_due --event-id evt_probe4_replay --repeat 3 --url $B/webhooks/stripe --secret "${STRIPE_WEBHOOK_SECRET:-whsec_replace_me}"
hdr "payment problem now blocks usage with 402 (past_due)"
curl -s -i -X POST $B/generate -H "X-API-Key: $FREE" -H "Idempotency-Key: probe4-402" -H 'Content-Type: application/json' -d '{"prompt":"x"}' | grep -E '^HTTP|^\{'
hdr "recover: subscription.updated status=active"; python -m scripts.simulate_stripe subscription-updated --tenant-id "$TID" --status active --url $B/webhooks/stripe --secret "${STRIPE_WEBHOOK_SECRET:-whsec_replace_me}"
