import asyncio
import json
import time

import httpx
import numpy as np
import pytest

from aml_memory.api import create_app
from aml_memory.config import Settings
from aml_memory.engine import Engine
from aml_memory.models import AcademicModels, ModelUnavailable
from aml_memory.schema import AddRequest


def body(request="r1"):
    return AddRequest(request_id=request, user_id="tenant", session_id="session",
                      messages=[{"role": "user", "content": "A silver telescope belongs to Talia."}])


def test_failed_embedding_does_not_ack_or_partially_commit(tmp_path):
    async def exercise():
        engine = Engine(Settings(data_dir=tmp_path, api_key="test", mode="hybrid", embedding_key="not-real"))
        async def fail(texts):
            raise ModelUnavailable("simulated network failure")
        engine.models.embed = fail
        with pytest.raises(ModelUnavailable):
            await engine.add(body())
        db = engine.store.connect("tenant")
        assert db.execute("SELECT count(*) FROM writes").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM docs").fetchone()[0] == 0
        db.close()
        await engine.models.close()
    asyncio.run(exercise())


def test_embedding_parsing_and_academic_model_lock(tmp_path):
    async def exercise():
        models = AcademicModels(Settings(data_dir=tmp_path, api_key="test", mode="hybrid", embedding_key="not-real"))
        seen = []
        async def handler(request):
            payload = json.loads(request.content)
            seen.append(payload)
            return httpx.Response(200, json={"data": [{"index": i, "embedding": [float(i + 1)] * 1024}
                                                       for i in reversed(range(len(payload["input"])))]})
        await models.http.aclose()
        models.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        vectors = await models.embed(["one", "two"])
        assert len(vectors) == 2 and all(np.isclose(np.linalg.norm(v), 1) for v in vectors)
        assert seen[0]["model"] == "text-embedding-v4"
        await models.close()
    asyncio.run(exercise())


def test_retention_removes_expired_content_and_search_index(tmp_path):
    async def exercise():
        engine = Engine(Settings(data_dir=tmp_path, api_key="test"))
        await engine.add(body())
        db = engine.store.connect("tenant")
        with db:
            for table in ("docs", "messages", "writes"):
                db.execute(f"UPDATE {table} SET added_at=?", (time.time() - 30 * 86400,))
        db.close()
        engine.store.purge_expired(29)
        db = engine.store.connect("tenant")
        for table in ("docs", "messages", "writes", "words", "trigrams"):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        db.close()
        await engine.models.close()
    asyncio.run(exercise())


def test_every_source_message_is_indexed_across_media_and_long_boundaries(tmp_path):
    for stride in (2, 4):
        engine = Engine(Settings(data_dir=tmp_path / str(stride), api_key="test", window_stride=stride))
        messages = [{"role": "user", "content": str(i)} for i in range(13)]
        messages[2]["content"] = [{"type": "text", "text": "media caption"}]
        messages[5]["content"] = "long source " * 900
        req = AddRequest(request_id="r", user_id="u", session_id="s", messages=messages)
        docs = engine.documents(req)
        covered = set(i for doc in docs for i in range(doc["start"], doc["end"]))
        assert covered == set(range(13))
        asyncio.run(engine.models.close())

