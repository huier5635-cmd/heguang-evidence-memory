import asyncio

import httpx
from fastapi.testclient import TestClient

from heguang_code.api import create_app
from heguang_code.config import Settings


def test_contract_auth_budget_and_conflicting_retries(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, api_key="test", max_body_bytes=3000))
    body = {"user_id": "one", "session_id": "session", "request_id": "request",
            "messages": [{"role": "assistant", "content": "src/cache.py cacheEntry() prevents stale reads."}]}
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.post("/add", json=body).status_code == 401
        headers = {"Authorization": "Token test"}
        response = client.post("/add", json=body, headers=headers)
        assert response.json() == {"success": True, **{name: body[name] for name in ("request_id", "user_id", "session_id")}}
        assert client.post("/add", json=body, headers=headers).json() == response.json()
        query = {"user_id": "one", "query": "cacheEntry stale", "top_k": 1}
        results = client.post("/search", json=query, headers={"X-Api-Key": "test"}).json()["data"]
        assert len(results) == 1 and "cacheEntry" in results[0]["content"]
        body["messages"][0]["content"] = "changed"
        assert client.post("/add", json=body, headers=headers).status_code == 409
        body["messages"][0]["content"] = "x" * 4000
        assert client.post("/add", json=body, headers=headers).status_code == 413
        assert client.post("/search", json={**query, "top_k": 101}, headers=headers).status_code == 422
        assert client.post("/search", json={**query, "user_id": "unknown"}, headers=headers).json() == {"data": []}


def test_concurrent_retry_and_health_bypass(tmp_path):
    async def run():
        app = create_app(Settings(data_dir=tmp_path, api_key="test", max_concurrency=1))
        body = {"user_id": "one", "session_id": "session", "request_id": "request",
                "messages": [{"role": "user", "content": "parallel_source()"}]}
        original = app.state.store.add
        def slow_add(req):
            entered.set()
            assert released.wait(5)
            original(req)
        import threading
        entered, released = threading.Event(), threading.Event()
        app.state.store.add = slow_add
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                     headers={"Authorization": "Bearer test"}) as client:
            first = asyncio.create_task(client.post("/add", json=body))
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                health = await asyncio.wait_for(client.get("/health"), 0.8)
                assert health.status_code == 200
            finally:
                released.set()
                assert (await first).status_code == 200
            retry = await asyncio.gather(*(client.post("/add", json=body) for _ in range(6)))
            assert all(row.status_code == 200 for row in retry)
        db = app.state.store.connect("one", create=False)
        assert db.execute("SELECT count(*) FROM sources").fetchone()[0] == 1
        db.close()
    asyncio.run(run())
