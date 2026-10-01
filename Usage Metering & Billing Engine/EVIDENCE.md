# EVIDENCE

One proof per Requirements box (brief, Section 6). Everything below was produced by running the code in this repo:
`python -m pytest -v` (SQLite) and `./scripts/demo_probes.sh` against a live `./run.sh` server. Reproduce with the same commands.

> **Environment note.** Captured in a sandbox with no Docker, no Stripe CLI and no route to `api.stripe.com`. So: SQLite instead of
> Postgres, and the offline mock checkout + `scripts/simulate_stripe.py` (real Stripe signature algorithm, real
> `stripe.Webhook.construct_event` verification) instead of `stripe listen` / `stripe trigger`. See README "Limitations".

## Full test run
```text
tests/test_jobs_and_migrations.py::test_alerts_enqueued_at_80_and_100_percent_once PASSED
tests/test_jobs_and_migrations.py::test_job_runner_delivers_alerts PASSED
tests/test_jobs_and_migrations.py::test_job_retries_then_fails_loudly PASSED
tests/test_jobs_and_migrations.py::test_alembic_migration_builds_schema_and_plans PASSED
tests/test_metering.py::test_same_request_twice_creates_one_event_and_mirrors_response PASSED
tests/test_metering.py::test_concurrent_retries_still_one_event PASSED
tests/test_metering.py::test_key_reuse_with_different_body_is_rejected PASSED
tests/test_metering.py::test_same_key_different_tenants_are_independent PASSED
tests/test_metering.py::test_idempotency_key_required_and_auth PASSED
tests/test_metering.py::test_validation_errors_are_clean_4xx_never_500 PASSED
tests/test_metering.py::test_tenant_isolation PASSED
tests/test_metering.py::test_usage_matches_pinned_pricing PASSED
tests/test_metering.py::test_events_outside_current_month_are_not_counted PASSED
tests/test_pricing.py::test_cached_input_is_cheaper_and_reasoning_bills_as_output PASSED
tests/test_pricing.py::test_categories_are_not_naively_added PASSED
tests/test_pricing.py::test_api_call_price_and_rounding_up PASSED
tests/test_pricing.py::test_invalid_usage_rejected PASSED
tests/test_quota.py::test_boundary_999_1000_1001_api_calls PASSED
tests/test_quota.py::test_rejected_request_is_not_stored_and_is_safe_to_retry PASSED
tests/test_quota.py::test_token_quota_no_partial_usage PASSED
tests/test_quota.py::test_free_plan_limits_and_upgrade_hint PASSED
tests/test_quota.py::test_past_due_gets_402_not_429 PASSED
tests/test_quota.py::test_concurrent_requests_never_exceed_quota PASSED
tests/test_stripe.py::test_checkout_mock_mode_returns_instructions PASSED
tests/test_stripe.py::test_checkout_completed_flips_free_to_pro_and_usage_shows_new_limits PASSED
tests/test_stripe.py::test_forged_signature_returns_400_and_changes_nothing PASSED
tests/test_stripe.py::test_duplicate_event_is_processed_once PASSED
tests/test_stripe.py::test_subscription_lifecycle_past_due_then_recovered_then_deleted PASSED
tests/test_stripe.py::test_out_of_order_events_do_not_regress_state PASSED
tests/test_stripe.py::test_unknown_tenant_and_unhandled_types_are_acked_not_errors PASSED
============================== 30 passed in 1.82s ==============================
```

---
## Metering
**[x] A billable action creates exactly one usage event, even under retries.**

Test names: `test_same_request_twice_creates_one_event_and_mirrors_response`, `test_concurrent_retries_still_one_event`
(8 parallel requests, same key -> 1 event), `test_key_reuse_with_different_body_is_rejected`.

```text
PROBE 1: same Idempotency-Key twice -> one event, identical response

$ POST /generate  (Idempotency-Key: probe1-key)   [attempt 1]
HTTP/1.1 200 OK
body.event_id = c32087e9-cb2b-4431-96d6-a5f9fa78b853 (body identical on both attempts)

$ POST /generate  (Idempotency-Key: probe1-key)   [attempt 2]
HTTP/1.1 200 OK
idempotent-replayed: true
body.event_id = c32087e9-cb2b-4431-96d6-a5f9fa78b853 (body identical on both attempts)

$ GET /usage/events  -> number of events recorded for this tenant
events: 1
```

## Quotas
**[x] Usage checked against plan; over-limit rejected. [x] Correct 429 / 402 with explanatory message.**

Tests: `test_boundary_999_1000_1001_api_calls`, `test_token_quota_no_partial_usage`, `test_past_due_gets_402_not_429`,
`test_concurrent_requests_never_exceed_quota` (10 parallel requests vs a 5-call limit -> exactly 5x200 + 5x429).

Live, against the real Free plan (1,000 calls):
```text
PROBE 2: drive a tenant to its EXACT quota (Free = 1000 API calls)
after 999 calls: {'used': 999, 'limit': 1000, 'remaining': 1}
call #1000 (exactly at limit) -> HTTP 200  quota={'used': 1000, 'limit': 1000, 'remaining': 0}
call #1001 (one past)         -> HTTP 429  Retry-After=2605109s
{"error":{"code":"quota_exceeded","message":"Monthly quota exceeded for: api_calls on the Free plan. Quota resets at 2026-11-01T00:00:00Z - upgrade to Pro for higher limits.","exceeded":[{"metric":"api_calls","used":1000,"requested":1,"limit":1000}],"upgrade_available":true}}
```

402 (payment problem) live:
```text
$ payment problem now blocks usage with 402 (past_due)
{"error":{"code":"payment_required","message":"Your subscription is past_due. Update your payment method to continue.","billing_status":"past_due"}}
```

## Cost calculation
**[x] Monthly usage rolls up to a cost per tenant. [x] Cached / reasoning / output priced correctly. [x] Constants pinned in config with proof.**

Constants: `app/config.py::PRICING` (fresh input $1.00/M, cached $0.25/M, output $4.00/M, reasoning = output, $0.002/API call).
Hand calculation for 5,000 input (4,000 cached) + 500 output + 1,500 reasoning + 1 call:

| category | tokens billed | rate / 1M | micro-USD |
|---|---|---|---|
| fresh input (5,000 - 4,000) | 1,000 | $1.00 | 1,000 |
| cached input | 4,000 | $0.25 | 1,000 |
| output | 500 | $4.00 | 2,000 |
| reasoning (as output) | 1,500 | $4.00 | 6,000 |
| API call | 1 | $0.002 | 2,000 |
| **total** | | | **12,000 = $0.012** |

Tests: `test_cached_input_is_cheaper_and_reasoning_bills_as_output`, `test_categories_are_not_naively_added`,
`test_api_call_price_and_rounding_up`, `test_usage_matches_pinned_pricing`. Live `GET /usage` matches the table:
```text
PROBE 5: pricing rules (same event as above: 5000 input incl. 4000 cached, 500 output, 1500 reasoning, 1 call)

$ GET /usage
{
  "usage": {
    "api_calls": {
      "used": 1,
      "limit": 1000,
      "remaining": 999
    },
    "ai_tokens": {
      "used": 7000,
      "limit": 100000,
      "remaining": 93000
    },
    "token_breakdown": {
      "input_tokens": 5000,
      "cached_input_tokens": 4000,
      "output_tokens": 500,
      "reasoning_tokens": 1500
    },
    "events": 1
  },
  "cost": {
    "api_calls_micro_usd": 2000,
    "fresh_input_micro_usd": 1000,
    "cached_input_micro_usd": 1000,
    "output_micro_usd": 2000,
    "reasoning_micro_usd": 6000,
    "tokens_micro_usd": 10000,
    "total_usage_micro_usd": 12000,
    "total_usage_display": "$0.012000",
    "plan_fee_micro_usd": 0,
    "amount_due_micro_usd": 0,
    "amount_due_display": "$0.000000",
    "note": "total_usage_* = metered value at list price; amount_due = plan fee (no overage in core)."
  }
}
```

## Stripe integration
**[x] Checkout works end-to-end. [x] Webhooks verify signatures, ignore duplicates, update plan/status.**

Tests: `test_checkout_completed_flips_free_to_pro_and_usage_shows_new_limits`, `test_forged_signature_returns_400_and_changes_nothing`
(wrong secret, missing header, tampered body, stale timestamp), `test_duplicate_event_is_processed_once`,
`test_subscription_lifecycle_past_due_then_recovered_then_deleted`, `test_out_of_order_events_do_not_regress_state`.

```text
PROBE 3: Checkout -> webhook flips Free -> Pro (offline mock checkout + signed webhook)

$ GET /usage (before)
plan: free | api_calls: {'used': 1, 'limit': 1000, 'remaining': 999} | ai_tokens: {'used': 7000, 'limit': 100000, 'remaining': 93000}

$ POST /billing/checkout {plan: pro}
{
  "mode": "mock",
  "session_id": "cs_mock_1251eead",
  "checkout_url": null,
  "tenant_id": "1251eead-d3f3-4e9b-9147-b27171e0b1aa",
  "message": "STRIPE_SECRET_KEY is not set: offline mock mode. Simulate payment with `python -m scripts.simulate_stripe checkout-completed --tenant-id 1251eead-d3f3-4e9b-9147-b27171e0b1aa`."
}

$ python -m scripts.simulate_stripe checkout-completed --tenant-id 1251eead-d3f3-4e9b-9147-b27171e0b1aa
[1/1] checkout.session.completed id=evt_cedd48aa25c344b7 -> HTTP 200 {"received":true,"outcome":"processed"}

$ GET /usage (after)
plan: pro | api_calls: {'used': 1, 'limit': 50000, 'remaining': 49999} | ai_tokens: {'used': 7000, 'limit': 5000000, 'remaining': 4993000} | amount_due: $20.000000
```

```text
PROBE 4: forged signature -> 400 ; real event replayed twice -> processed once

$ python -m scripts.simulate_stripe forged
[1/1] checkout.session.completed id=evt_3d4418c96ae9445c -> HTTP 400 {"error":{"code":"invalid_signature","message":"Webhook signature verification failed."}}

$ replay the SAME event id three times
[1/3] customer.subscription.updated id=evt_probe4_replay -> HTTP 200 {"received":true,"outcome":"processed"}
[2/3] customer.subscription.updated id=evt_probe4_replay -> HTTP 200 {"received":true,"outcome":"duplicate"}
[3/3] customer.subscription.updated id=evt_probe4_replay -> HTTP 200 {"received":true,"outcome":"duplicate"}

$ payment problem now blocks usage with 402 (past_due)
HTTP/1.1 402 Payment Required
{"error":{"code":"payment_required","message":"Your subscription is past_due. Update your payment method to continue.","billing_status":"past_due"}}

$ recover: subscription.updated status=active
[1/1] customer.subscription.updated id=evt_9084caba0c8945d7 -> HTTP 200 {"received":true,"outcome":"processed"}
```

## Data model, tests & documentation
**[x] Database has tenants, plans, subscriptions, usage events; data isolated per tenant.**
Alembic migration `migrations/versions/0001_initial.py` (test `test_alembic_migration_builds_schema_and_plans` runs `alembic upgrade head`
on a fresh DB and checks tables, the `uq_usage_tenant_idem` unique constraint and seeded plans). Isolation: `test_tenant_isolation`.

**[x] README + architecture diagram + setup; required files present.**
`README.md` (diagram + run steps), `capstone.yaml`, `EVIDENCE.md`, `BUILDLOG.md`, `.env.example`, `.gitignore` (contains `.env`).

## Shared requirements (Section 12)
| # | Requirement | Where / proof |
|---|---|---|
| 1 | Layered architecture | `app/api` (HTTP) -> `app/services` (logic) -> `app/models.py` + `migrations` (data) |
| 2 | Validation at the boundary | `test_validation_errors_are_clean_4xx_never_500` (negative, cached>input, wrong type, huge, unknown field, malformed JSON -> 422) |
| 3 | Background job, retries + failure alert | `test_alerts_enqueued_at_80_and_100_percent_once`, `test_job_runner_delivers_alerts`, `test_job_retries_then_fails_loudly` (asserts the `ALERT: job ... failed permanently` log) |
| 4 | Real persistence, migrations, indexes, isolated tenants | Alembic 0001; indexes on `(tenant_id, created_at)`, `api_key_hash`, Stripe ids, `(status, run_at)` |
| 5 | Idempotency where it matters | usage events (`UNIQUE(tenant_id, idempotency_key)` + lock) and webhooks (`stripe_events.id` PK) |
| 6 | Secrets clean | `.env` git-ignored, `.env.example` placeholders, API keys stored as SHA-256, only `sk_test_` accepted, no secret is logged |
| 7 | Cost tracked, if AI is used | N/A for model calls (tokens are simulated, no model is called); the service itself is the cost tracker |

## Do the tests have teeth? (mutation check)
I deliberately broke the code three ways and confirmed the suite fails each time, then restored the code:
1. Removed the per-tenant lock -> `test_concurrent_requests_never_exceed_quota` and the alert-count test failed.
2. Changed `>` to `>=` in the quota check (off-by-one) -> `test_boundary_999_1000_1001_api_calls` failed.
3. Removed the webhook dedupe insert -> `test_duplicate_event_is_processed_once` failed (`UNIQUE constraint failed: stripe_events.id`).
