import hashlib
import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import clock
from app.errors import ApiError
from app.models import Plan, Tenant


def hash_key(api_key: str) -> str:
    # API keys are 256-bit random, so a plain SHA-256 is sufficient (no salt/slow hash needed).
    return hashlib.sha256(api_key.encode()).hexdigest()


def create_tenant(session: Session, name: str, plan_id: str = "free", api_key: str | None = None):
    if session.get(Plan, plan_id) is None:
        raise ApiError(422, "unknown_plan", f"Unknown plan '{plan_id}'.")
    api_key = api_key or "mk_" + secrets.token_urlsafe(32)
    tenant = Tenant(id=str(uuid.uuid4()), name=name, api_key_hash=hash_key(api_key),
                    plan_id=plan_id, billing_status="active", created_at=clock.utcnow())
    session.add(tenant)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise ApiError(409, "tenant_exists", f"A tenant named '{name}' already exists.")
    return tenant, api_key


def find_by_api_key(session: Session, api_key: str) -> Tenant | None:
    return session.scalar(select(Tenant).where(Tenant.api_key_hash == hash_key(api_key)))
