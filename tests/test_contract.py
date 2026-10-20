import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from aml_memory.api import create_app
from aml_memory.config import Settings


@pytest.fixture
def app(tmp_path):
    return create_app(Settings(data_dir=tmp_path, api_key="test-secret"))


@pytest.fixture
def client(app):
    with TestClient(app, headers={"Authorization": "Bearer test-secret"}) as client:
        yield client


def add_body(user="u1", request="r1", session="s1", text="Mira bought a blue bicycle on 5 March 2026."):
    return {"request_id": request, "user_id": user, "session_id": session,
            "messages": [{"role": "user", "timestamp": 1704067200000, "content": text}]}


def search(client, user="u1", query="What color is Mira's bicycle?", top_k=100):
    return client.post("/search", json={"query": query, "user_id": user, "top_k": top_k})


def test_atomic_add_echo_and_immediate_search(client):
    body = add_body(user="eval:test/../user", session="s:☃")
    response = client.post("/add", json=body)
    assert response.status_code == 200
    assert response.json() == {"success": True, **{k: body[k] for k in ("request_id", "user_id", "session_id")}}
    data = search(client, user=body["user_id"], top_k=1).json()["data"]
    assert len(data) == 1 and "blue bicycle" in data[0]["content"]


def test_user_isolation_and_session_unification(client):
    client.post("/add", json=add_body(text="Mira's secret number is 3817."))
    client.post("/add", json=add_body(user="u2", text="Mira's secret number is 9406."))
    client.post("/add", json=add_body(request="r2", session="other", text="Mira's new address is Cedar Street."))
    result = json.dumps(search(client, query="Mira secret number and new address").json())
    assert "3817" in result and "Cedar Street" in result and "9406" not in result
    assert search(client, user="never-written").json() == {"data": []}


def test_idempotency_conflict_and_restart(client, app):
    body = add_body()
    assert client.post("/add", json=body).status_code == 200
    first = search(client).json()
    assert client.post("/add", json=body).status_code == 200
    assert search(client).json() == first
    conflict = add_body(text="A changed write reusing the same request.")
    assert client.post("/add", json=conflict).status_code == 409
    new_app = create_app(app.state.engine.settings)
    with TestClient(new_app, headers={"X-Api-Key": "test-secret"}) as restarted:
        assert search(restarted).json() == first


def test_auth_health_and_invalid_inputs(app, client):
    with TestClient(app) as anonymous:
        assert anonymous.get("/health").status_code == 200
        assert anonymous.post("/add", json=add_body()).status_code == 401
    for key in ("request_id", "messages", "user_id", "session_id"):
        body = add_body()
        del body[key]
        assert client.post("/add", json=body).status_code == 422
    for k in (0, -1, 101, 1.5, True, "1"):
        assert search(client, top_k=k).status_code == 422


def test_multimodal_parts_and_nonempty_validation(client):
    body = add_body()
    parts = [{"type": "text", "text": "The photo shows a red bicycle."},
             {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGVsbG8="}}]
    body["messages"][0]["content"] = parts
    assert client.post("/add", json=body).status_code == 200
    content = search(client, query="red bicycle").json()["data"][0]["content"]
    assert content[1:] == parts
    for bad in ("", " ", [], [{"type": "image_url", "image_url": {"url": "https://private-host/image.png"}}]):
        body = add_body(request="invalid")
        body["messages"][0]["content"] = bad
        assert client.post("/add", json=body).status_code == 422


def test_long_source_no_loss_and_unicode(client):
    source = "甲乙丙丁 骑自行车旅行。" * 800 + "TAIL_EVIDENCE_692"
    body = add_body(text=source)
    assert client.post("/add", json=body).status_code == 200
    rows = search(client, query="TAIL_EVIDENCE_692").json()["data"]
    assert "TAIL_EVIDENCE_692" in rows[0]["content"]
    joined = "".join(row["content"] for row in rows)
    assert "甲乙丙丁" in joined


def test_concurrent_retries_commit_once(app):
    async def exercise():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", headers={"Authorization": "Token test-secret"}) as http:
            responses = await asyncio.gather(*[http.post("/add", json=add_body()) for _ in range(8)])
            assert all(r.status_code == 200 for r in responses)
            return (await http.post("/search", json={"user_id": "u1", "query": "bicycle", "top_k": 100})).json()
    result = asyncio.run(exercise())
    assert len(result["data"]) == 1


def test_model_misconfiguration_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="EMBEDDING_KEY"):
        Settings(data_dir=tmp_path, mode="hybrid", api_key="test")
    with pytest.raises(ValueError, match="AML_API_KEY"):
        Settings(data_dir=tmp_path)

