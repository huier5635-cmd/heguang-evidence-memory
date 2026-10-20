import asyncio
import json

import httpx
import numpy as np

from aml_memory.config import Settings
from aml_memory.engine import Engine
from aml_memory.models import AcademicModels
from aml_memory.schema import AddRequest, SearchRequest
from aml_memory.store import Store


def request(messages=None):
    return AddRequest(request_id="batch", user_id="one", session_id="session",
                      messages=messages or [{"role": "user", "content": "A source memory."}])


def test_research_batches_unique_source_and_maps_tags_only_to_quoted_windows(tmp_path):
    async def exercise():
        engine = Engine(Settings(data_dir=tmp_path, api_key="test", mode="research", embedding_key="fake", llm_key="fake"))
        calls = []
        async def tags(source):
            calls.append(source)
            return [{"quote": "message 9 owns a telescope", "tags": ["astronomy"]},
                    {"quote": "This does not exist anywhere", "tags": ["inventedkeyword"]}]
        async def embed(texts):
            return [np.ones(1024, dtype=np.float32) / 32 for _ in texts]
        engine.models.source_tags, engine.models.embed = tags, embed
        req = request([{"role": "user", "content": f"message {i} owns a telescope"} for i in range(20)])
        await engine.add(req)
        await engine.add(req)  # Retried Add must not call a model again.
        assert len(calls) == 1
        db = engine.store.connect("one")
        indexed = list(db.execute("SELECT id FROM words WHERE words MATCH 'astronomy'"))
        assert len(indexed) == 2  # Two source windows contain message 9.
        assert not list(db.execute("SELECT id FROM words WHERE words MATCH 'inventedkeyword'"))
        assert all("astronomy" not in row[0] for row in db.execute("SELECT content FROM docs"))
        db.close()
        await engine.models.close()
    asyncio.run(exercise())


def test_model_tags_require_exact_source_anchor(tmp_path):
    async def exercise():
        models = AcademicModels(Settings(data_dir=tmp_path, api_key="test"))
        async def post(*args):
            return {"choices": [{"message": {"content": json.dumps({"entries": [
                {"quote": "Ada studies stars", "tags": ["astronomy"]},
                {"quote": "Ada studies planets", "tags": ["fabricated"]}]})}}]}
        models.post = post
        assert await models.source_tags("Ada studies stars") == [{"quote": "Ada studies stars", "tags": ["astronomy"]}]
        await models.close()
    asyncio.run(exercise())


def test_sparse_candidates_do_not_materialize_unrelated_user_corpus(tmp_path):
    store = Store(tmp_path, "test")
    docs = [{"id": f"doc_{i:04}", "start": i, "end": i + 1,
             "content": "source " * 500, "text": "source " * 500,
             "index_text": "uniqueneedle" if i == 1999 else "other material"}
            for i in range(2000)]
    store.commit(request(), "test", docs)
    selected, rankings = store.candidates("one", "uniqueneedle", [], 5)
    assert set(selected) == {"doc_1999"}
    assert all(key in selected for ranking, _ in rankings for key in ranking)
    fallback, _ = store.candidates("one", "the and or", [], 5)
    assert len(fallback) == 5 and sorted(fallback) == [f"doc_{i:04}" for i in range(5)]


def test_streamed_dense_matches_full_exact_ranking_and_reaches_outside_sparse_pool(tmp_path):
    async def exercise():
        engine = Engine(Settings(data_dir=tmp_path, api_key="test", mode="hybrid", embedding_key="fake"))
        random = np.random.default_rng(17)
        matrix = random.normal(size=(610, 1024)).astype(np.float32)
        matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
        query = matrix[-1].copy()
        docs = [{"id": f"doc_{i:04}", "start": i, "end": i + 1,
                 "content": f"Original source number {i}", "text": "source", "index_text": "source",
                 "vector": vector.tobytes()} for i, vector in enumerate(matrix)]
        engine.store.commit(request(), "test", docs)
        expected = sorted(range(len(matrix)), key=lambda i: (-float(matrix[i] @ query), f"doc_{i:04}"))[:20]
        assert engine.store.dense_top("one", query, 20, batch_size=7) == [f"doc_{i:04}" for i in expected]
        async def embed(texts):
            return [query for _ in texts]
        engine.models.embed = embed
        result = await engine.search(SearchRequest(user_id="one", query="unmatchedquasar", top_k=5))
        assert result[0]["id"] == "doc_0609"
        assert await engine.search(SearchRequest(user_id="other", query="unmatchedquasar", top_k=5)) == []
        await engine.models.close()
    asyncio.run(exercise())


def test_model_http_concurrency_is_bounded_across_add_segments(tmp_path):
    async def exercise():
        models = AcademicModels(Settings(data_dir=tmp_path, api_key="test", max_concurrency=2))
        in_flight, peak = 0, 0
        async def handler(request):
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1
            return httpx.Response(200, json={"ok": True})
        await models.http.aclose()
        models.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        results = await asyncio.gather(*(models.post("https://model.example", "/test", "fake", {}) for _ in range(12)))
        assert len(results) == 12 and peak == 2
        await models.close()
    asyncio.run(exercise())
