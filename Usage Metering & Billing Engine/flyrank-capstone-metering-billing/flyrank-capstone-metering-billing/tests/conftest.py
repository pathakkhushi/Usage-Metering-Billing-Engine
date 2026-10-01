import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ.update({
    "DATABASE_URL": f"sqlite:///{_tmp}/test.db",
    "RUN_WORKER": "false",
    "STRIPE_SECRET_KEY": "",
    "STRIPE_WEBHOOK_SECRET": "whsec_test_secret",
    "ADMIN_TOKEN": "test-admin",
    "JOB_BACKOFF_SECONDS": "0",
})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402
from app.models import Plan  # noqa: E402

ADMIN = {"X-Admin-Token": "test-admin"}


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    from scripts.seed import seed
    with SessionLocal.begin() as s:
        seed(s)
        s.add(Plan(id="tiny", name="Tiny", api_call_limit=5, token_limit=1000, monthly_fee_cents=0))
    yield


@pytest.fixture
def client():
    with TestClient(fastapi_app) as c:
        yield c


def make_tenant(client, name="t1", plan="free"):
    r = client.post("/tenants", json={"name": name, "plan": plan}, headers=ADMIN)
    assert r.status_code == 201, r.text
    d = r.json()
    return d["tenant_id"], {"X-API-Key": d["api_key"]}


def gen(client, headers, key, usage=None, prompt="hi"):
    body = {"prompt": prompt}
    if usage is not None:
        body["usage"] = usage
    return client.post("/generate", json=body, headers={**headers, "Idempotency-Key": key})
