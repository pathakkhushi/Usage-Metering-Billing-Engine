from sqlalchemy import update

from app.db import SessionLocal
from app.models import Tenant
from tests.conftest import gen, make_tenant


def test_boundary_999_1000_1001_api_calls(client):
    # Free plan = 1000 calls. Pre-fill 998 events is slow via HTTP; use the 5-call 'tiny' plan instead.
    _, h = make_tenant(client, "tiny-co", plan="tiny")
    for i in range(4):                                  # 4 of 5 used
        assert gen(client, h, f"c{i}").status_code == 200
    r = gen(client, h, "c4")                            # exactly AT the limit (5/5): allowed
    assert r.status_code == 200 and r.json()["quota"]["api_calls"] == {"used": 5, "limit": 5, "remaining": 0}
    r = gen(client, h, "c5")                            # one past the limit
    assert r.status_code == 429
    err = r.json()["error"]
    assert err["code"] == "quota_exceeded" and "quota exceeded" in err["message"].lower()
    assert int(r.headers["Retry-After"]) > 0
    assert err["exceeded"][0] == {"metric": "api_calls", "used": 5, "requested": 1, "limit": 5}
    assert client.get("/usage", headers=h).json()["usage"]["api_calls"]["used"] == 5  # nothing recorded


def test_rejected_request_is_not_stored_and_is_safe_to_retry(client):
    _, h = make_tenant(client, "tiny-co", plan="tiny")
    assert gen(client, h, "big", {"input_tokens": 1000}).status_code == 200      # tokens 1000/1000
    assert gen(client, h, "retry", {"input_tokens": 1}).status_code == 429
    assert gen(client, h, "retry", {"input_tokens": 1}).status_code == 429       # same key: re-evaluated, not cached
    assert len(client.get("/usage/events", headers=h).json()["events"]) == 1


def test_token_quota_no_partial_usage(client):
    _, h = make_tenant(client, "tiny-co", plan="tiny")
    assert gen(client, h, "a", {"input_tokens": 600}).status_code == 200         # 600/1000
    r = gen(client, h, "b", {"input_tokens": 300, "output_tokens": 101})         # 401 > 400 remaining
    assert r.status_code == 429 and r.json()["error"]["exceeded"][0]["metric"] == "ai_tokens"
    assert gen(client, h, "c", {"input_tokens": 300, "output_tokens": 100}).status_code == 200  # exactly 1000


def test_free_plan_limits_and_upgrade_hint(client):
    _, h = make_tenant(client)
    r = gen(client, h, "huge", {"input_tokens": 100_001})
    assert r.status_code == 429 and r.json()["error"]["upgrade_available"] is True
    assert client.get("/usage", headers=h).json()["usage"]["api_calls"]["limit"] == 1000


def test_past_due_gets_402_not_429(client):
    tid, h = make_tenant(client, "pro-co", plan="pro")
    with SessionLocal.begin() as s:
        s.execute(update(Tenant).where(Tenant.id == tid).values(billing_status="past_due"))
    r = gen(client, h, "k")
    assert r.status_code == 402 and r.json()["error"]["code"] == "payment_required"


def test_concurrent_requests_never_exceed_quota(client):
    from concurrent.futures import ThreadPoolExecutor
    _, h = make_tenant(client, "tiny-co", plan="tiny")
    with ThreadPoolExecutor(10) as ex:
        codes = sorted(r.status_code for r in ex.map(lambda i: gen(client, h, f"x{i}"), range(10)))
    assert codes == [200] * 5 + [429] * 5
    assert client.get("/usage", headers=h).json()["usage"]["api_calls"]["used"] == 5
