import json
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import Tenant, UsageEvent
from tests.conftest import gen, make_tenant


def event_count():
    with SessionLocal() as s:
        return s.scalar(select(func.count(UsageEvent.id)))


def test_same_request_twice_creates_one_event_and_mirrors_response(client):
    _, h = make_tenant(client)
    u = {"input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 50, "reasoning_tokens": 10}
    r1 = gen(client, h, "key-1", u)
    r2 = gen(client, h, "key-1", u)
    assert r1.status_code == r2.status_code == 200
    assert r1.json() == r2.json()
    assert r2.headers["Idempotent-Replayed"] == "true" and "Idempotent-Replayed" not in r1.headers
    assert event_count() == 1
    usage = client.get("/usage", headers=h).json()["usage"]
    assert usage["api_calls"]["used"] == 1 and usage["ai_tokens"]["used"] == 160  # 100 + 50 + 10


def test_concurrent_retries_still_one_event(client):
    _, h = make_tenant(client)
    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(lambda _: gen(client, h, "race", {"input_tokens": 10}), range(8)))
    assert all(r.status_code == 200 for r in res)
    assert len({json.dumps(r.json(), sort_keys=True) for r in res}) == 1
    assert event_count() == 1


def test_key_reuse_with_different_body_is_rejected(client):
    _, h = make_tenant(client)
    assert gen(client, h, "k", {"input_tokens": 1}).status_code == 200
    r = gen(client, h, "k", {"input_tokens": 2})
    assert r.status_code == 422 and r.json()["error"]["code"] == "idempotency_key_reuse"
    assert event_count() == 1


def test_same_key_different_tenants_are_independent(client):
    _, a = make_tenant(client, "a")
    _, b = make_tenant(client, "b")
    assert gen(client, a, "shared").status_code == 200
    assert gen(client, b, "shared").status_code == 200
    assert event_count() == 2


def test_idempotency_key_required_and_auth(client):
    _, h = make_tenant(client)
    assert client.post("/generate", json={}, headers=h).status_code == 400
    assert client.post("/generate", json={}, headers={"Idempotency-Key": "x"}).status_code == 401
    assert client.get("/usage", headers={"X-API-Key": "nope"}).status_code == 401


def test_validation_errors_are_clean_4xx_never_500(client):
    _, h = make_tenant(client)
    bad = [{"input_tokens": -1}, {"input_tokens": 5, "cached_input_tokens": 9}, {"input_tokens": "abc"},
           {"input_tokens": 10**12}, {"bogus": 1}]
    for i, usage in enumerate(bad):
        r = gen(client, h, f"bad-{i}", usage)
        assert r.status_code == 422, (usage, r.text)
        assert r.json()["error"]["code"] == "validation_error"
    assert client.post("/generate", content=b"{not json", headers={**h, "Idempotency-Key": "j",
                       "Content-Type": "application/json"}).status_code == 422
    assert event_count() == 0


def test_tenant_isolation(client):
    ta, a = make_tenant(client, "a")
    _, b = make_tenant(client, "b")
    gen(client, a, "only-a")
    assert client.get("/usage/events", headers=b).json()["events"] == []
    assert len(client.get("/usage/events", headers=a).json()["events"]) == 1
    assert client.get("/usage", headers=b).json()["usage"]["api_calls"]["used"] == 0


def test_usage_matches_pinned_pricing(client):
    _, h = make_tenant(client)
    gen(client, h, "p1", {"input_tokens": 5000, "cached_input_tokens": 4000, "output_tokens": 500, "reasoning_tokens": 1500})
    cost = client.get("/usage", headers=h).json()["cost"]
    assert cost["fresh_input_micro_usd"] == 1_000 and cost["cached_input_micro_usd"] == 1_000
    assert cost["output_micro_usd"] == 2_000 and cost["reasoning_micro_usd"] == 6_000
    assert cost["api_calls_micro_usd"] == 2_000                      # 1 call * $0.002
    assert cost["total_usage_micro_usd"] == 12_000 and cost["total_usage_display"] == "$0.012000"
    assert cost["amount_due_micro_usd"] == 0                        # Free plan fee


def test_events_outside_current_month_are_not_counted(client, monkeypatch):
    from datetime import datetime
    from app import clock
    _, h = make_tenant(client)
    monkeypatch.setattr(clock, "utcnow", lambda: datetime(2026, 1, 31, 23, 59, 59))
    gen(client, h, "jan")
    assert client.get("/usage", headers=h).json()["usage"]["api_calls"]["used"] == 1
    monkeypatch.setattr(clock, "utcnow", lambda: datetime(2026, 2, 1, 0, 0, 0))
    assert client.get("/usage", headers=h).json()["usage"]["api_calls"]["used"] == 0  # quota resets
