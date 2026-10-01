import os
import subprocess
import sys
import tempfile

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Job
from app.services import jobs
from tests.conftest import gen, make_tenant


def test_alerts_enqueued_at_80_and_100_percent_once(client):
    _, h = make_tenant(client, "tiny-co", plan="tiny")   # 5 calls / 1000 tokens
    for i in range(3):
        gen(client, h, f"a{i}")                            # 3/5 = 60%
    with SessionLocal() as s:
        assert s.scalars(select(Job)).all() == []
    gen(client, h, "a3")                                   # 4/5 = 80% -> alert
    gen(client, h, "a4")                                   # 5/5 = 100% -> alert
    gen(client, h, "a4")                                   # replay -> no new job
    with SessionLocal() as s:
        keys = sorted(j.dedupe_key.split(":", 2)[2] for j in s.scalars(select(Job)))
    assert [k.split(":", 1)[1] for k in keys] == ["api_calls:100", "api_calls:80"]


def test_job_runner_delivers_alerts(client, monkeypatch):
    sent = []
    monkeypatch.setitem(jobs.HANDLERS, "usage_alert", lambda p: sent.append(p))
    _, h = make_tenant(client, "tiny-co", plan="tiny")
    for i in range(4):
        gen(client, h, f"a{i}")
    assert jobs.run_due_jobs() == {"done": 1, "retried": 0, "failed": 0}
    assert sent[0]["threshold_pct"] == 80 and sent[0]["used"] == 4
    assert jobs.run_due_jobs() == {"done": 0, "retried": 0, "failed": 0}   # not re-sent


def test_job_retries_then_fails_loudly(client, monkeypatch, caplog):
    def boom(_):
        raise ConnectionError("alert endpoint down")
    monkeypatch.setitem(jobs.HANDLERS, "usage_alert", boom)
    _, h = make_tenant(client, "tiny-co", plan="tiny")
    for i in range(4):
        gen(client, h, f"a{i}")
    with SessionLocal.begin() as s:
        s.scalars(select(Job)).first().max_attempts = 3
    results = [jobs.run_due_jobs() for _ in range(3)]
    assert [r["retried"] for r in results] == [1, 1, 0] and results[2]["failed"] == 1
    assert any("ALERT: job" in r.message and "failed permanently" in r.message for r in caplog.records)
    with SessionLocal() as s:
        j = s.scalars(select(Job)).first()
        assert j.status == "failed" and j.attempts == 3 and "alert endpoint down" in j.last_error


def test_alembic_migration_builds_schema_and_plans():
    with tempfile.TemporaryDirectory() as d:
        url = f"sqlite:///{d}/mig.db"
        env = {**os.environ, "ALEMBIC_DATABASE_URL": url}
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True, env=env)
        from sqlalchemy import create_engine, inspect, text
        eng = create_engine(url)
        assert {"plans", "tenants", "subscriptions", "usage_events", "stripe_events", "jobs"} <= set(inspect(eng).get_table_names())
        uq = [u["name"] for u in inspect(eng).get_unique_constraints("usage_events")]
        assert "uq_usage_tenant_idem" in uq
        with eng.connect() as c:
            rows = c.execute(text("select id, api_call_limit, token_limit from plans order by id")).all()
        assert rows == [("free", 1000, 100000), ("pro", 50000, 5000000)]
