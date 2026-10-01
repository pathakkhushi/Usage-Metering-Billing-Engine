"""Data layer: tables only, no business logic."""
from datetime import datetime

from sqlalchemy import (BigInteger, DateTime, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Plan(Base):
    __tablename__ = "plans"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    api_call_limit: Mapped[int] = mapped_column(BigInteger)
    token_limit: Mapped[int] = mapped_column(BigInteger)
    monthly_fee_cents: Mapped[int] = mapped_column(Integer)


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    api_key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id"), default="free")
    billing_status: Mapped[str] = mapped_column(String(16), default="active")  # active|past_due|unpaid
    meter_lock: Mapped[int] = mapped_column(BigInteger, default=0)  # row-lock handle, see metering.py
    created_at: Mapped[datetime] = mapped_column(DateTime)


class Subscription(Base):
    """Mirror of Stripe's subscription for a tenant. Written ONLY by verified webhooks."""
    __tablename__ = "subscriptions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), unique=True)
    stripe_customer_id: Mapped[str | None] = mapped_column(String(64), index=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32))  # raw Stripe status
    last_event_created: Mapped[int] = mapped_column(BigInteger, default=0)  # out-of-order guard
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class UsageEvent(Base):
    """One row per billable action. UNIQUE(tenant, idempotency_key) is the no-double-count guarantee."""
    __tablename__ = "usage_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_usage_tenant_idem"),
        Index("ix_usage_tenant_created", "tenant_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"))
    idempotency_key: Mapped[str] = mapped_column(String(255))
    request_hash: Mapped[str] = mapped_column(String(64))
    api_calls: Mapped[int] = mapped_column(Integer, default=1)
    input_tokens: Mapped[int] = mapped_column(BigInteger, default=0)          # total prompt tokens (includes cached)
    cached_input_tokens: Mapped[int] = mapped_column(BigInteger, default=0)   # subset of input_tokens
    output_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    reasoning_tokens: Mapped[int] = mapped_column(BigInteger, default=0)      # billed as output
    response_json: Mapped[str] = mapped_column(Text)                          # original response, replayed on retry
    created_at: Mapped[datetime] = mapped_column(DateTime)


class StripeEvent(Base):
    """Webhook dedupe: PRIMARY KEY on Stripe's event id."""
    __tablename__ = "stripe_events"
    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    type: Mapped[str] = mapped_column(String(120))
    created: Mapped[int] = mapped_column(BigInteger)
    outcome: Mapped[str] = mapped_column(String(64))
    processed_at: Mapped[datetime] = mapped_column(DateTime)


class Job(Base):
    """Tiny DB-backed job queue (outbox) for background work with retries."""
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status_run_at", "status", "run_at"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(64))
    payload: Mapped[str] = mapped_column(Text)
    dedupe_key: Mapped[str | None] = mapped_column(String(255), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|done|failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    run_at: Mapped[datetime] = mapped_column(DateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime)
