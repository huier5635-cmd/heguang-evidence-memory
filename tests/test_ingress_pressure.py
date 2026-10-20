"""ASGI contract regressions for bounded request-body admission; no model calls."""
import asyncio
import json

import httpx
import pytest

from aml_memory.api import create_app
from aml_memory.config import Settings
from aml_memory.models import ModelUnavailable


def payload(request="first"):
    return {"request_id": request, "user_id": "one", "session_id": "session",
            "messages": [{"role": "user", "content": "A synthetic copper telescope."}]}


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                             headers={"Authorization": "Bearer test", "Content-Type": "application/json"})


def test_queued_body_is_not_consumed_and_health_stays_available(tmp_path):
    async def exercise():
        app = create_app(Settings(data_dir=tmp_path, api_key="test", max_concurrency=1))
        started, release, read = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def add(req):
            if req.request_id == "first":
                started.set()
                await release.wait()

        async def stream():
            read.set()
            yield json.dumps(payload("second")).encode()

        app.state.engine.add = add
        async with client(app) as http:
            first = asyncio.create_task(http.post("/add", json=payload()))
            second = None
            try:
                await asyncio.wait_for(started.wait(), 2)
                second = asyncio.create_task(http.post("/add", content=stream()))
                assert (await asyncio.wait_for(http.get("/health"), 1)).status_code == 200
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(read.wait(), 0.1)
                release.set()
                responses = await asyncio.wait_for(asyncio.gather(first, second), 2)
                assert [r.status_code for r in responses] == [200, 200]
                assert read.is_set()
            finally:
                release.set()
                await asyncio.gather(*(t for t in (first, second) if t), return_exceptions=True)
                await app.state.engine.models.close()
    asyncio.run(exercise())


def test_auth_rejection_does_not_consume_body_or_wait_for_slot(tmp_path):
    async def exercise():
        app = create_app(Settings(data_dir=tmp_path, api_key="test", max_concurrency=1))
        started, release = asyncio.Event(), asyncio.Event()

        async def add(req):
            started.set()
            await release.wait()

        async def unread():
            raise AssertionError("Unauthenticated request body was consumed")
            yield b""  # Async generator, intentionally never iterated.

        app.state.engine.add = add
        async with client(app) as http:
            first = asyncio.create_task(http.post("/add", json=payload()))
            try:
                await asyncio.wait_for(started.wait(), 2)
                denied = await asyncio.wait_for(http.post("/add", content=unread(),
                                                         headers={"Authorization": "Bearer wrong"}), 1)
                assert denied.status_code == 401
            finally:
                release.set()
                await first
                await app.state.engine.models.close()
    asyncio.run(exercise())


def test_stream_limit_without_content_length_releases_slot(tmp_path):
    async def exercise():
        app = create_app(Settings(data_dir=tmp_path, api_key="test", max_concurrency=1, max_body_bytes=1024))
        async def oversized():
            for _ in range(5):
                yield b"x" * 300

        async with client(app) as http:
            response = await http.post("/add", content=oversized())
            assert "content-length" not in response.request.headers
            assert response.status_code == 413
            after = await asyncio.wait_for(http.post("/add", json=payload()), 2)
            assert after.status_code == 200
            rows = (await http.post("/search", json={"user_id": "one", "query": "copper", "top_k": 1})).json()["data"]
            assert len(rows) == 1 and "telescope" in rows[0]["content"]
        await app.state.engine.models.close()
    asyncio.run(exercise())


def test_cancelled_queued_upload_does_not_leak_capacity(tmp_path):
    async def exercise():
        app = create_app(Settings(data_dir=tmp_path, api_key="test", max_concurrency=1))
        started, release = asyncio.Event(), asyncio.Event()
        async def add(req):
            if req.request_id == "first":
                started.set()
                await release.wait()

        app.state.engine.add = add
        async with client(app) as http:
            first = asyncio.create_task(http.post("/add", json=payload()))
            queued = None
            try:
                await asyncio.wait_for(started.wait(), 2)
                queued = asyncio.create_task(http.post("/add", json=payload("cancelled")))
                await asyncio.wait_for(http.get("/health"), 1)
                queued.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await queued
                release.set()
                assert (await first).status_code == 200
                assert (await asyncio.wait_for(http.post("/add", json=payload("after")), 2)).status_code == 200
            finally:
                release.set()
                await asyncio.gather(*(t for t in (first, queued) if t), return_exceptions=True)
                await app.state.engine.models.close()
    asyncio.run(exercise())


def test_model_failure_releases_admission_slot(tmp_path):
    async def exercise():
        app = create_app(Settings(data_dir=tmp_path, api_key="test", max_concurrency=1))
        async def add(req):
            if req.request_id == "first":
                raise ModelUnavailable("simulated failure")
        app.state.engine.add = add
        async with client(app) as http:
            assert (await http.post("/add", json=payload())).status_code == 503
            assert (await asyncio.wait_for(http.post("/add", json=payload("after")), 2)).status_code == 200
        await app.state.engine.models.close()
    asyncio.run(exercise())
