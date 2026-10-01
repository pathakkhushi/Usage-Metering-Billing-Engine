# Usage Metering & Billing Engine

A backend service that answers the three questions every SaaS must answer:
**how much has this tenant used, what does it cost, and have they hit their limit?**
Python · FastAPI · SQLAlchemy · Alembic · Stripe (test mode). No AI key needed - tokens are simulated.

## Quick start (clean machine, Python 3.11+)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # defaults work as-is (SQLite, offline mock checkout)
./run.sh                        # 1) alembic upgrade head  2) uvicorn on :8000
python -m scripts.seed          # (second terminal) demo tenants + API keys
python -m pytest                # optional: 30 tests
./scripts/demo_probes.sh        # optional: replays all 5 acceptance probes against the live server
```
(For `demo_probes.sh`, start the server with `ADMIN_TOKEN` / `STRIPE_WEBHOOK_SECRET` equal to what you export in the second terminal.)

Try it:
```bash
KEY=demo_key_acme_free_0000000000000000
curl -s -X POST localhost:8000/generate -H "X-API-Key: $KEY" -H "Idempotency-Key: abc-1" \
     -H 'Content-Type: application/json' \
     -d '{"prompt":"hi","usage":{"input_tokens":5000,"cached_input_tokens":4000,"output_tokens":500,"reasoning_tokens":1500}}'
curl -s localhost:8000/usage -H "X-API-Key: $KEY"
```
Interactive docs: http://localhost:8000/docs

## Architecture

```
 Client --X-API-Key--> HTTP layer (app/api, deps.py, schemas.py): routing, auth, validation -> clean 4xx, never 500
                              |                                         |
                              v                                         v
   POST /generate -> MeterService.record(tenant, key, qty)      POST /billing/checkout -> Stripe Checkout (test mode)
                       1 lock tenant row (first statement)                                     |  customer pays (4242...)
                       2 idempotency key seen? -> replay original response                     v
                       3 billing past_due/unpaid? -> 402            Stripe --signed webhook--> POST /webhooks/stripe
                       4 used + requested > limit? -> 429 + Retry-After     1 verify signature (forged -> 400)
                       5 INSERT usage_event (+ outbox alert job)            2 INSERT stripe_events(id PK) (replay -> ignored)
                                |                                           3 skip stale / out-of-order events
   GET /usage <- rollup(usage_events) -> {used, limit, cost}                4 update subscription + tenant plan/status
                                |
   Background worker (services/jobs.py): usage_alert jobs, exponential-backoff retries, failure alert
                                |
   Data layer (models.py + Alembic): plans, tenants, subscriptions, usage_events, stripe_events, jobs
   SQLite (default) or Postgres via DATABASE_URL
```

## Plans & quotas (monthly, UTC calendar month)

| Plan | API calls | AI tokens | Fee |
|------|-----------|-----------|-----|
| Free | 1,000 | 100,000 | $0 |
| Pro  | 50,000 | 5,000,000 | $20.00 |

## Documented rules

**Idempotency.** `POST /generate` requires an `Idempotency-Key` header. Same tenant + same key + same body = **one** usage
event; the retry returns the original response with `Idempotent-Replayed: true`. Same key + *different* body
-> `422 idempotency_key_reuse`. Enforced by a per-tenant lock **and** `UNIQUE(tenant_id, idempotency_key)`.

**Boundary rule.** A request is allowed iff `used + requested <= limit`. At 999/1000 a 1-call request succeeds
(-> 1000/1000, `remaining: 0`); the next one is rejected. A request that would overshoot *either* dimension is rejected as a
whole - no partial usage. Rejected requests record nothing and don't consume the idempotency key.

**402 vs 429.**

| Status | Meaning | Example |
|---|---|---|
| `429 quota_exceeded` | Plan is fine, monthly allowance used up. Has `Retry-After` (seconds until reset) and `exceeded[]` detail; Free tenants also get `upgrade_available: true`. | 1,001st call on Free |
| `402 payment_required` | Billing problem: subscription `past_due` / `unpaid`. Fix payment, not wait. | failed renewal |

**Token pricing** (constants pinned in `app/config.py`; integer micro-USD, 1 USD = 1,000,000):

| Category | Rate / 1M tokens | Rule |
|---|---|---|
| fresh input = `input_tokens - cached_input_tokens` | $1.00 | |
| cached input | $0.25 | cheaper; a **subset of** `input_tokens`, never double-billed |
| output | $4.00 | |
| reasoning | $4.00 | billed **as output** (asserted equal to the output rate at import time) |
| API call | $0.002 each | |

Each category is rounded **up** to the next micro-USD once, on monthly totals. Quota unit "AI tokens" =
`input + output + reasoning` (cached is already inside input). Worked example: 5,000 input (4,000 cached) + 500 output +
1,500 reasoning = 1,000 + 1,000 + 2,000 + 6,000 = 10,000 micro-USD ($0.01).

`GET /usage` returns `total_usage_*` (metered value at list price) and `amount_due` (= plan fee; overage is a stretch goal).

**Stripe sync.** Payment truth lives at Stripe; the DB mirrors it via verified events only
(`checkout.session.completed`, `customer.subscription.updated`, `customer.subscription.deleted`). Dedupe by event-id primary key
in the *same transaction* as the state change; events older than the last applied one are ignored.

## API summary

| Method | Path | Auth | Notes |
|---|---|---|---|
| POST | `/tenants` | `X-Admin-Token` | create tenant; API key returned once (stored only as SHA-256) |
| POST | `/generate` | `X-API-Key` + `Idempotency-Key` | the dummy billable endpoint (1 API call + simulated tokens) |
| GET | `/usage` | `X-API-Key` | used / limit / remaining + cost for this month |
| GET | `/usage/events` | `X-API-Key` | this tenant's events only |
| POST | `/billing/checkout` | `X-API-Key` | `{ "plan": "pro" }` -> Stripe Checkout session |
| POST | `/webhooks/stripe` | `Stripe-Signature` | verified, deduplicated |

## Stripe test mode - two ways to run

**A. Real Stripe test mode** (free, no card): create a recurring Price in the Stripe dashboard (test mode), then in `.env` set
`STRIPE_SECRET_KEY=sk_test_...`, `STRIPE_PRICE_ID_PRO=price_...`. Then:
```bash
stripe listen --forward-to localhost:8000/webhooks/stripe     # prints a whsec_... -> put in STRIPE_WEBHOOK_SECRET, restart
curl -X POST localhost:8000/billing/checkout -H "X-API-Key: $KEY" -H 'Content-Type: application/json' -d '{"plan":"pro"}'
# open checkout_url, pay with 4242 4242 4242 4242 (any future expiry/CVC) -> webhook flips the tenant to Pro
stripe events resend <evt_id>                                  # replay -> outcome "duplicate"
```
Only `sk_test_` keys are accepted; live keys are refused.

**B. Offline mock** (default when `STRIPE_SECRET_KEY` is empty; used for the evidence in this repo):
`POST /billing/checkout` returns a mock session, and `python -m scripts.simulate_stripe ...` sends Stripe-shaped events
signed with Stripe's real algorithm (`t=...,v1=HMAC_SHA256(secret, "t.payload")`). Verification is the real
`stripe.Webhook.construct_event`, not a stub.

## Background job
Crossing 80% or 100% of a quota enqueues a `usage_alert` job **in the same transaction** as the usage event (outbox, deduped per
tenant/month/metric/threshold). A worker thread delivers it (log line, plus POST to `ALERT_WEBHOOK_URL` if set), retrying with
exponential backoff; after `JOB_MAX_ATTEMPTS` failures it is marked `failed` and a `CRITICAL ... ALERT` log line is emitted.

## Layout
```
app/api, deps.py, schemas.py      HTTP: routing, auth, validation
app/services/                     logic: metering, pricing, usage, billing, webhooks, jobs, tenants
app/models.py, db.py, migrations/ data: tables + Alembic migration (0001 also inserts Free/Pro)
scripts/                          seed.py, simulate_stripe.py (like `stripe trigger`), demo_probes.sh
tests/                            30 tests: pricing, idempotency, races, boundaries, webhooks, jobs, migration
```

## Optional: Postgres
`docker compose up` runs the API with Postgres (same code; only `DATABASE_URL` changes). Locking uses a row-level `UPDATE`
on the tenant, which works on both databases.

## Limitations (honest)
- Verified on SQLite only; the Postgres path (`docker-compose.yml`) was not run (no Docker in the build environment). SQLite serializes all writers; Postgres locks per tenant row.
- Real Stripe API calls (`Checkout.Session.create`) were not exercised (network to Stripe was blocked while building); webhook signature verification and all state sync were.
- Month rollups are computed on the fly using an index on `(tenant_id, created_at)`; at very high volume you would add pre-aggregated counters.
- Per-event `estimated_cost` can differ by a few micro-USD from the monthly figure, because the monthly cost prices summed totals once (by design).
- No overage billing, invoices or proration (stretch goals). Alert delivery is a log/webhook, not email.
- Demo API keys in `scripts/seed.py` are public: local demos only.
