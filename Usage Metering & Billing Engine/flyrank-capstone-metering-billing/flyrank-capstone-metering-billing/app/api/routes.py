"""HTTP layer only: parse/validate -> call a service -> shape the response."""
import json
import logging
import math

import stripe
from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.deps import current_tenant_id, require_admin
from app.errors import ApiError
from app.models import Tenant, UsageEvent
from app.schemas import CheckoutRequest, GenerateRequest, TenantCreate, TokenUsage
from app.services import billing, metering, tenants, usage, webhooks

log = logging.getLogger("metering.http")
router = APIRouter()


@router.get("/health")
def health():
    return {"status": "ok"}


@router.post("/tenants", status_code=201, dependencies=[Depends(require_admin)])
def create_tenant(body: TenantCreate):
    with SessionLocal.begin() as s:
        t, api_key = tenants.create_tenant(s, body.name, body.plan)
        return {"tenant_id": t.id, "name": t.name, "plan": t.plan_id,
                "api_key": api_key, "note": "Store this key now - it is shown only once."}


@router.post("/generate")
def generate(response: Response, body: GenerateRequest, tenant_id: str = Depends(current_tenant_id),
             idempotency_key: str | None = Header(None)):
    """Dummy billable endpoint: records one API call + simulated AI tokens."""
    if not idempotency_key or not (1 <= len(idempotency_key) <= 255):
        raise ApiError(400, "idempotency_key_required", "Send an Idempotency-Key header (1-255 chars).")
    if body.usage is not None:
        u = body.usage
    else:  # deterministic simulation from the prompt (so replays are identical)
        approx = max(1, math.ceil(len(body.prompt) / 4))
        u = TokenUsage(input_tokens=approx, output_tokens=approx * 2)
    req = metering.UsageRequest(1, u.input_tokens, u.cached_input_tokens, u.output_tokens, u.reasoning_tokens)
    fingerprint = metering.request_fingerprint({"prompt": body.prompt, "usage": u.model_dump()})
    result, replayed = metering.record(
        tenant_id, idempotency_key, fingerprint, req,
        extra_response={"output": f"(simulated completion for {len(body.prompt)} prompt chars)"})
    if replayed:
        response.headers["Idempotent-Replayed"] = "true"
    return result


@router.get("/usage")
def get_usage(tenant_id: str = Depends(current_tenant_id)):
    with SessionLocal() as s:
        return usage.usage_report(s, s.get(Tenant, tenant_id))


@router.get("/usage/events")
def list_events(tenant_id: str = Depends(current_tenant_id), limit: int = Query(50, ge=1, le=200),
                offset: int = Query(0, ge=0)):
    with SessionLocal() as s:
        rows = s.scalars(select(UsageEvent).where(UsageEvent.tenant_id == tenant_id)
                         .order_by(UsageEvent.created_at.desc(), UsageEvent.id).limit(limit).offset(offset)).all()
        return {"events": [{"id": e.id, "idempotency_key": e.idempotency_key, "api_calls": e.api_calls,
                            "input_tokens": e.input_tokens, "cached_input_tokens": e.cached_input_tokens,
                            "output_tokens": e.output_tokens, "reasoning_tokens": e.reasoning_tokens,
                            "created_at": e.created_at.isoformat() + "Z"} for e in rows]}


@router.post("/billing/checkout")
def checkout(body: CheckoutRequest, tenant_id: str = Depends(current_tenant_id)):
    with SessionLocal() as s:
        return billing.create_checkout_session(s, s.get(Tenant, tenant_id), body.plan)


@router.post("/webhooks/stripe")
async def stripe_webhook(request: Request, stripe_signature: str | None = Header(None)):
    payload = await request.body()  # RAW bytes: signature is computed over the exact body
    cfg = get_settings()
    if not stripe_signature:
        raise ApiError(400, "missing_signature", "Missing Stripe-Signature header.")
    try:
        stripe.Webhook.construct_event(payload, stripe_signature, cfg.stripe_webhook_secret,
                                       tolerance=cfg.webhook_tolerance_seconds)
        event = json.loads(payload)
    except (stripe.SignatureVerificationError, ValueError):
        raise ApiError(400, "invalid_signature", "Webhook signature verification failed.")
    outcome = webhooks.process_event(event)
    log.info("stripe event %s (%s) -> %s", event.get("id"), event.get("type"), outcome)
    return {"received": True, "outcome": outcome}
