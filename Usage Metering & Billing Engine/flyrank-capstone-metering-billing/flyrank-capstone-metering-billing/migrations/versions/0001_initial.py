"""initial schema + Free/Pro plans

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None


def upgrade() -> None:
    plans = op.create_table(
        "plans",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("api_call_limit", sa.BigInteger, nullable=False),
        sa.Column("token_limit", sa.BigInteger, nullable=False),
        sa.Column("monthly_fee_cents", sa.Integer, nullable=False),
    )
    op.create_table(
        "tenants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("api_key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("plan_id", sa.String(32), sa.ForeignKey("plans.id"), nullable=False),
        sa.Column("billing_status", sa.String(16), nullable=False),
        sa.Column("meter_lock", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_tenants_api_key_hash", "tenants", ["api_key_hash"])
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False, unique=True),
        sa.Column("stripe_customer_id", sa.String(64)),
        sa.Column("stripe_subscription_id", sa.String(64)),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("last_event_created", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_subscriptions_customer", "subscriptions", ["stripe_customer_id"])
    op.create_index("ix_subscriptions_subscription", "subscriptions", ["stripe_subscription_id"])
    op.create_table(
        "usage_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("api_calls", sa.Integer, nullable=False),
        sa.Column("input_tokens", sa.BigInteger, nullable=False),
        sa.Column("cached_input_tokens", sa.BigInteger, nullable=False),
        sa.Column("output_tokens", sa.BigInteger, nullable=False),
        sa.Column("reasoning_tokens", sa.BigInteger, nullable=False),
        sa.Column("response_json", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_usage_tenant_idem"),
    )
    op.create_index("ix_usage_tenant_created", "usage_events", ["tenant_id", "created_at"])
    op.create_table(
        "stripe_events",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("type", sa.String(120), nullable=False),
        sa.Column("created", sa.BigInteger, nullable=False),
        sa.Column("outcome", sa.String(64), nullable=False),
        sa.Column("processed_at", sa.DateTime, nullable=False),
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("payload", sa.Text, nullable=False),
        sa.Column("dedupe_key", sa.String(255), unique=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("max_attempts", sa.Integer, nullable=False),
        sa.Column("run_at", sa.DateTime, nullable=False),
        sa.Column("last_error", sa.Text),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_jobs_status_run_at", "jobs", ["status", "run_at"])
    op.bulk_insert(plans, [
        {"id": "free", "name": "Free", "api_call_limit": 1000, "token_limit": 100000, "monthly_fee_cents": 0},
        {"id": "pro", "name": "Pro", "api_call_limit": 50000, "token_limit": 5000000, "monthly_fee_cents": 2000},
    ])


def downgrade() -> None:
    for t in ("jobs", "stripe_events", "usage_events", "subscriptions", "tenants", "plans"):
        op.drop_table(t)
