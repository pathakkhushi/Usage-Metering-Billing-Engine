"""The metering path: lock tenant -> idempotency check -> payment/quota check -> insert event.

Everything happens in ONE transaction, serialized per tenant:
  * the very first statement is `UPDATE tenants SET meter_lock = meter_lock + 1`, which takes a
    row lock on Postgres and the write lock on SQLite, so concurrent requests for a tenant queue up;
  * UNIQUE(tenant_id, idempotency_key) is the final backstop.
Documented boundary rule: a request is allowed iff used + requested <= limit.
(999/1000 -> a 1-call request is allowed; 1000/1000 -> the next one is rejected with 429.)
Rejected requests record NOTHING and consume no idempotency key, so they are safe to retry.
"""
import hashlib
import json
import uuid
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app import clock
from app.db import SessionLocal
from app.errors import ApiError
from app.models import Job, Plan, Tenant, UsageEvent
from app.services import pricing
from app.services.usage import quota_view, rollup

ALERT_THRESHOLDS = (80, 100)


@dataclass
class UsageRequest:
    api_calls: int = 1
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def ai_tokens(self) -> int:
        return pricing.total_tokens(self.input_tokens, self.output_tokens, self.reasoning_tokens)


def request_fingerprint(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def record(tenant_id: str, idem_key: str, fingerprint: str, req: UsageRequest, extra_response: dict) -> tuple[dict, bool]:
    """Returns (response_body, replayed). Raises ApiError(402|422|429)."""
    for attempt in (1, 2):
        try:
            return _record_once(tenant_id, idem_key, fingerprint, req, extra_response)
        except IntegrityError:
            # Lost a race on the UNIQUE constraint (shouldn't happen under the lock); the retry
            # will find the winner's row and replay it.
            if attempt == 2:
                raise
    raise AssertionError("unreachable")


def _record_once(tenant_id, idem_key, fingerprint, req, extra_response):
    now = clock.utcnow()
    with SessionLocal.begin() as s:
        # 1. serialize this tenant's metering (must be the first statement of the transaction)
        s.execute(update(Tenant).where(Tenant.id == tenant_id).values(meter_lock=Tenant.meter_lock + 1))

        # 2. idempotency: same key -> original result, no new event
        existing = s.scalar(select(UsageEvent).where(UsageEvent.tenant_id == tenant_id,
                                                     UsageEvent.idempotency_key == idem_key))
        if existing is not None:
            if existing.request_hash != fingerprint:
                raise ApiError(422, "idempotency_key_reuse",
                               "This Idempotency-Key was already used with a different request body.")
            return json.loads(existing.response_json), True

        tenant = s.get(Tenant, tenant_id)
        plan = s.get(Plan, tenant.plan_id)

        # 3. payment state -> 402
        if tenant.billing_status in ("past_due", "unpaid"):
            raise ApiError(402, "payment_required",
                           f"Your subscription is {tenant.billing_status}. Update your payment method to continue.",
                           billing_status=tenant.billing_status)

        # 4. quota -> 429 (exceeding either dimension rejects the WHOLE request; no partial usage)
        start, end = clock.period_bounds(now)
        used = rollup(s, tenant_id, start, end)
        exceeded = []
        if used["api_calls"] + req.api_calls > plan.api_call_limit:
            exceeded.append(("api_calls", used["api_calls"], req.api_calls, plan.api_call_limit))
        if used["ai_tokens"] + req.ai_tokens > plan.token_limit:
            exceeded.append(("ai_tokens", used["ai_tokens"], req.ai_tokens, plan.token_limit))
        if exceeded:
            retry_after = max(int((end - now).total_seconds()), 1)
            names = ", ".join(e[0] for e in exceeded)
            raise ApiError(
                429, "quota_exceeded",
                f"Monthly quota exceeded for: {names} on the {plan.name} plan. "
                f"Quota resets at {end.isoformat()}Z"
                + (" - upgrade to Pro for higher limits." if plan.id == "free" else "."),
                headers={"Retry-After": str(retry_after)},
                exceeded=[{"metric": m, "used": u, "requested": r, "limit": l} for m, u, r, l in exceeded],
                upgrade_available=plan.id == "free",
            )

        # 5. record the event (+ original response, for exact replays)
        event_id = str(uuid.uuid4())
        new_used = {"api_calls": used["api_calls"] + req.api_calls, "ai_tokens": used["ai_tokens"] + req.ai_tokens}
        cost = pricing.compute_cost(req.api_calls, req.input_tokens, req.cached_input_tokens,
                                    req.output_tokens, req.reasoning_tokens)
        body = {
            "event_id": event_id,
            "idempotency_key": idem_key,
            **extra_response,
            "usage_recorded": {
                "api_calls": req.api_calls, "input_tokens": req.input_tokens,
                "cached_input_tokens": req.cached_input_tokens, "output_tokens": req.output_tokens,
                "reasoning_tokens": req.reasoning_tokens, "ai_tokens": req.ai_tokens,
            },
            "estimated_cost_micro_usd": cost.total_micro,
            "estimated_cost_display": pricing.fmt_usd(cost.total_micro),
            "quota": {"api_calls": quota_view(new_used["api_calls"], plan.api_call_limit),
                      "ai_tokens": quota_view(new_used["ai_tokens"], plan.token_limit)},
        }
        s.add(UsageEvent(
            id=event_id, tenant_id=tenant_id, idempotency_key=idem_key, request_hash=fingerprint,
            api_calls=req.api_calls, input_tokens=req.input_tokens,
            cached_input_tokens=req.cached_input_tokens, output_tokens=req.output_tokens,
            reasoning_tokens=req.reasoning_tokens, response_json=json.dumps(body), created_at=now))
        s.flush()
        _enqueue_alerts(s, tenant, plan, start, used, new_used, now)
        return body, False


def _enqueue_alerts(s, tenant, plan, start, before, after, now):
    """Outbox: alert jobs are written in the SAME transaction as the usage event."""
    from app.config import get_settings
    for metric, limit in (("api_calls", plan.api_call_limit), ("ai_tokens", plan.token_limit)):
        for pct in ALERT_THRESHOLDS:
            threshold = limit * pct  # compare in integers: used*100 >= limit*pct
            if before[metric] * 100 < threshold <= after[metric] * 100:
                key = f"alert:{tenant.id}:{start:%Y-%m}:{metric}:{pct}"
                if s.scalar(select(Job.id).where(Job.dedupe_key == key)) is None:
                    s.add(Job(kind="usage_alert", dedupe_key=key, status="pending", attempts=0,
                              max_attempts=get_settings().job_max_attempts, run_at=now, created_at=now,
                              payload=json.dumps({"tenant_id": tenant.id, "tenant": tenant.name, "metric": metric,
                                                  "threshold_pct": pct, "used": after[metric], "limit": limit,
                                                  "plan": plan.id})))
