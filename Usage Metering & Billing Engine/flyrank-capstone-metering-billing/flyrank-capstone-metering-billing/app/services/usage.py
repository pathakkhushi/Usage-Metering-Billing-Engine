"""Read side: monthly rollup of usage_events -> used / limit / cost."""
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import clock
from app.models import Plan, Tenant, UsageEvent
from app.services import pricing


def rollup(session: Session, tenant_id: str, start: datetime, end: datetime) -> dict:
    row = session.execute(
        select(
            func.coalesce(func.sum(UsageEvent.api_calls), 0),
            func.coalesce(func.sum(UsageEvent.input_tokens), 0),
            func.coalesce(func.sum(UsageEvent.cached_input_tokens), 0),
            func.coalesce(func.sum(UsageEvent.output_tokens), 0),
            func.coalesce(func.sum(UsageEvent.reasoning_tokens), 0),
            func.count(UsageEvent.id),
        ).where(UsageEvent.tenant_id == tenant_id,
                UsageEvent.created_at >= start, UsageEvent.created_at < end)
    ).one()
    calls, inp, cached, out, reasoning, n = (int(x) for x in row)
    return {"api_calls": calls, "input_tokens": inp, "cached_input_tokens": cached,
            "output_tokens": out, "reasoning_tokens": reasoning,
            "ai_tokens": pricing.total_tokens(inp, out, reasoning), "events": n}


def quota_view(used: int, limit: int) -> dict:
    return {"used": used, "limit": limit, "remaining": max(limit - used, 0)}


def usage_report(session: Session, tenant: Tenant) -> dict:
    plan = session.get(Plan, tenant.plan_id)
    start, end = clock.period_bounds(clock.utcnow())
    r = rollup(session, tenant.id, start, end)
    cost = pricing.compute_cost(r["api_calls"], r["input_tokens"], r["cached_input_tokens"],
                                r["output_tokens"], r["reasoning_tokens"])
    fee_micro = plan.monthly_fee_cents * 10_000  # 1 cent = 10,000 micro-USD
    return {
        "tenant_id": tenant.id,
        "plan": {"id": plan.id, "name": plan.name, "monthly_fee_cents": plan.monthly_fee_cents},
        "billing_status": tenant.billing_status,
        "period": {"start": start.isoformat() + "Z", "end": end.isoformat() + "Z"},
        "usage": {
            "api_calls": quota_view(r["api_calls"], plan.api_call_limit),
            "ai_tokens": quota_view(r["ai_tokens"], plan.token_limit),
            "token_breakdown": {k: r[k] for k in ("input_tokens", "cached_input_tokens",
                                                  "output_tokens", "reasoning_tokens")},
            "events": r["events"],
        },
        "cost": {
            **cost.as_dict(),
            "plan_fee_micro_usd": fee_micro,
            "amount_due_micro_usd": fee_micro,  # core scope: flat fee, no overage (stretch goal)
            "amount_due_display": pricing.fmt_usd(fee_micro),
            "note": "total_usage_* = metered value at list price; amount_due = plan fee (no overage in core).",
        },
    }
