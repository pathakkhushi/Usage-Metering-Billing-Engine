# BUILDLOG - AI usage log

## Who did what (honest summary)
This repository was **generated end-to-end by an AI assistant (Claude)** from the capstone brief, then executed and tested in
the assistant's sandbox. If you are the intern submitting this: before you submit, read every file, run it yourself, and
be able to explain any 2-3 lines an evaluator picks (the idempotency block in `metering.py`, the webhook dedupe in
`webhooks.py`, the rounding in `pricing.py` are the likeliest). Replace/extend this log with your own notes - "the AI wrote it"
is not an acceptable answer in review.

## Where AI helped
- Chose the architecture from the brief: one metering path, one read path, one payment-sync path.
- Wrote all code, tests, migration, docs. Ran the suite (30 tests) and the 5 probes against a live server.
- Mutation-checked the tests (removed the tenant lock, flipped `>` to `>=`, removed webhook dedupe) to confirm they fail when the code is wrong.

## Where the AI had to adapt / things that went wrong (and the fix)
| Problem | What happened | Change |
|---|---|---|
| No Docker in the build sandbox | Could not run Postgres | **SQLite default** (explicitly allowed by the brief); code stays Postgres-compatible; `docker-compose.yml` provided but **untested** |
| No Stripe CLI; `api.stripe.com` returned 403 | Could not run `stripe listen`, `stripe trigger` or create real Checkout sessions | Added **offline mock checkout** + `scripts/simulate_stripe.py` that signs events with Stripe's real HMAC scheme; verification still uses the real `stripe.Webhook.construct_event`. Real-mode code path exists but was **not exercised** |
| First test run: 25 errors | `import app.models` in `conftest.py` shadowed the FastAPI `app` object | Renamed import (`fastapi_app`) |
| `Worker` thread | I named an attribute `_stop`, which collides with `threading.Thread._stop` | Renamed to `_halt` |
| Demo script | Background server did not survive between tool calls; first probe run hit "connection refused" | Restarted the server detached; re-ran |
| `pkill -f` | My cleanup command matched its own shell and killed it, so files were not written | Re-ran with a safer pattern |

## Design decisions to be able to defend
- **One transaction per metered request**, first statement is `UPDATE tenants SET meter_lock = meter_lock + 1`: a row lock on Postgres, the write lock on SQLite, so no dialect-specific `FOR UPDATE` is needed and quota checks can't race.
- **Cached tokens are a subset of input tokens** (provider-style), so fresh input = input - cached; reasoning bills at the output rate; quota tokens = input + output + reasoning.
- **429 vs 402**: 429 = allowance used up (wait or upgrade); 402 = payment problem (`past_due`/`unpaid`).
- **Webhook dedupe and state change share one transaction**, so a failed apply never leaves a "processed" marker behind.
- Alerts use an **outbox**: the alert job is committed atomically with the usage event.

## Known gaps / not verified
- Postgres path, Dockerfile/compose, and live Stripe API calls were not run. No overage, invoices, proration (stretch goals).
