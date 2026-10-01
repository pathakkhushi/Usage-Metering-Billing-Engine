"""Seed demo data (idempotent). DEMO keys are fixed and public - never use them outside local demos."""
from sqlalchemy import select

from app import clock
from app.db import SessionLocal
from app.models import Plan, Tenant
from app.plans import PLAN_DEFS
from app.services import tenants

DEMO = [("acme-free", "free", "demo_key_acme_free_0000000000000000"),
        ("globex-pro", "pro", "demo_key_globex_pro_0000000000000000")]


def seed(session):
    for pid, name, calls, tokens, fee in PLAN_DEFS:
        if session.get(Plan, pid) is None:
            session.add(Plan(id=pid, name=name, api_call_limit=calls, token_limit=tokens, monthly_fee_cents=fee))
    session.flush()
    for name, plan, key in DEMO:
        if session.scalar(select(Tenant).where(Tenant.name == name)) is None:
            tenants.create_tenant(session, name, plan, api_key=key)


if __name__ == "__main__":
    with SessionLocal.begin() as s:
        seed(s)
    print("Seeded. Demo API keys (X-API-Key):")
    for name, plan, key in DEMO:
        print(f"  {name:12s} [{plan}]  {key}")
