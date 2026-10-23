import asyncio
import json
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from heguang_code.api import create_app
from heguang_code.config import Settings
from heguang_code.schema import AddRequest
from heguang_code.store import Store, digest, streaming_digest


def test_streamed_write_checksum_preserves_existing_canonical_contract():
    values = [None, {}, [True, False, None, -0.0, 1.25e-8],
              {"unicode": "\u03c3\u0307\U0001f9e0", "quoted": 'x\\y\n"z', "long": "\u03c3\n" * 20000}]
    assert all(streaming_digest(value) == digest(value) for value in values)


def test_legacy_committed_request_remains_idempotent(tmp_path):
    store = Store(Settings(data_dir=tmp_path, api_key="test"))
    request = AddRequest(user_id="same", request_id="legacy", session_id="session", messages=[{
        "role": "assistant", "content": "Unicode \u03c3 and code fix_packet()"}])
    # Simulate a pre-change committed digest and prove the new write takes the
    # exact retry path without creating any source rows.
    db = store.connect(request.user_id)
    db.execute("INSERT INTO writes VALUES(?,?,?)", (request.request_id, digest(request.model_dump()), 1))
    db.commit()
    db.close()
    store.add(request)
    db = store.connect(request.user_id, create=False)
    assert db.execute("SELECT count(*) FROM sources").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM writes").fetchone()[0] == 1
    db.close()


@pytest.mark.parametrize("field", ["request_id", "user_id", "session_id"])
def test_invalid_unicode_identity_is_a_permanent_client_error(tmp_path, field):
    app = create_app(Settings(data_dir=tmp_path, api_key="test"))
    payload = {"request_id": "one", "user_id": "user", "session_id": "session",
               "messages": [{"role": "user", "content": "valid \u03c3"}]}
    payload[field] = "bad\ud800"
    with TestClient(app) as client:
        response = client.post("/add", content=json.dumps(payload), headers={
            "Authorization": "Bearer test", "Content-Type": "application/json"})
        assert response.status_code == 422
    assert list(tmp_path.glob("*.sqlite3")) == []


def test_invalid_request_does_not_echo_a_large_source(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, api_key="test"))
    with TestClient(app) as client:
        response = client.post("/add", json={"request_id": "one", "user_id": "user", "session_id": "session",
            "messages": [{"role": "user", "content": "x" * 100000, "timestamp": "invalid"}]},
            headers={"Authorization": "Bearer test"})
        assert response.status_code == 422 and len(response.content) < 1024
        assert "x" * 256 not in response.text


def test_waiting_large_add_does_not_take_the_small_processing_slot(tmp_path):
    async def run():
        app = create_app(Settings(data_dir=tmp_path, api_key="test", large_body_bytes=1024))
        original = app.state.store.add
        first_entered, second_entered, release = threading.Event(), threading.Event(), threading.Event()
        def slow(req):
            if req.user_id.startswith("large"):
                (first_entered if req.user_id == "large1" else second_entered).set()
                assert release.wait(5)
            original(req)
        app.state.store.add = slow
        def payload(user, size):
            return {"user_id": user, "request_id": "one", "session_id": "session",
                    "messages": [{"role": "user", "content": "x" * size}]}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                headers={"Authorization": "Bearer test"}) as client:
            first = asyncio.create_task(client.post("/add", json=payload("large1", 2000)))
            assert await asyncio.to_thread(first_entered.wait, 2)
            second = asyncio.create_task(client.post("/add", json=payload("large2", 2000)))
            try:
                small = await asyncio.wait_for(client.post("/add", json=payload("small", 20)), 1)
                assert small.status_code == 200 and not second_entered.is_set()
                assert (await client.get("/health")).status_code == 200
            finally:
                release.set()
                assert all(row.status_code == 200 for row in await asyncio.gather(first, second))
    asyncio.run(run())


def test_unknown_chunked_length_is_supported_and_false_length_is_rejected(tmp_path):
    async def run():
        app = create_app(Settings(data_dir=tmp_path, api_key="test"))
        body = json.dumps({"request_id": "one", "session_id": "session", "user_id": "user",
                           "messages": [{"role": "user", "content": "source_packet()"}]}).encode()
        async def chunks():
            yield body[:20]
            yield body[20:]
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                headers={"Authorization": "Bearer test", "Content-Type": "application/json"}) as client:
            assert (await client.post("/add", content=chunks())).status_code == 200
            bad = await client.post("/add", content=body, headers={"Content-Length": "10"})
            assert bad.status_code == 400
    asyncio.run(run())
