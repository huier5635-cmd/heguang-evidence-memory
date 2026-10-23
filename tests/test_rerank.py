import asyncio
import math
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from heguang_code.api import create_app
from heguang_code.config import Settings
from heguang_code.reranker import PairScorer, fit_budget, reorder
from heguang_code.schema import SearchRequest


@pytest.mark.parametrize("value", [0, 3, -1, True, 1.0])
def test_unbounded_or_noninteger_model_threads_rejected(tmp_path, value):
    with pytest.raises(ValueError, match="threads must be 1 or 2"):
        Settings(data_dir=tmp_path, api_key="test", reranker_threads=value)
    # Reject before loading model dependencies or touching an absent model path.
    with pytest.raises(ValueError, match="threads must be 1 or 2"):
        PairScorer(tmp_path / "absent-model", threads=value)


def test_single_core_environment_reaches_model_and_health(tmp_path, monkeypatch):
    import heguang_code.api as api_module
    observed = []

    class ConfiguredScorer:
        def __init__(self, directory, *, threads):
            observed.append((directory, threads))

    monkeypatch.setenv("HCM_API_KEY", "test")
    monkeypatch.setenv("HCM_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HCM_RERANKER_DIR", str(tmp_path / "model"))
    monkeypatch.setenv("HCM_RERANK_THREADS", "1")
    monkeypatch.setattr(api_module, "PairScorer", ConfiguredScorer)
    settings = Settings.from_env()
    app = api_module.create_app(settings)
    with TestClient(app) as client:
        assert client.get("/health").json()["reranker_threads"] == 1
    assert observed == [(tmp_path / "model", 1)]


def test_reranking_preserves_all_evidence_and_tail_order():
    rows = [{"id": str(index), "content": "exact source " + str(index), "score": 1 / (index + 1)} for index in range(25)]
    result = reorder(rows, list(range(16)))
    assert {row["id"]: row["content"] for row in result} == {row["id"]: row["content"] for row in rows}
    assert result[16:] == [{**row, "score": result[16 + i]["score"]} for i, row in enumerate(rows[16:])]
    assert all(result[i]["score"] >= result[i + 1]["score"] for i in range(len(result) - 1))
    assert rows[0]["score"] == 1  # The original pool is never mutated.
    with pytest.raises(RuntimeError, match="Invalid reranker"):
        reorder(rows, [math.nan])


def test_api_reranks_a_bounded_pool_before_requested_top_k(tmp_path):
    class Scorer:
        selected = None
        def scores(self, query, documents):
            assert len(documents) <= 16 and all(not text.startswith("[source ") for text in documents)
            return [100 if text == self.selected else index for index, text in enumerate(documents)]
    scorer = Scorer()
    app = create_app(Settings(data_dir=tmp_path, api_key="test"), scorer=scorer)
    with TestClient(app, headers={"X-Api-Key": "test"}) as client:
        body = {"user_id": "u", "session_id": "s", "request_id": "r", "messages": [
            {"role": "tool", "content": f"candidate_probe unique evidence {index:02}"} for index in range(25)]}
        assert client.post("/add", json=body).status_code == 200
        pool = app.state.store.search(SearchRequest(user_id="u", query="candidate_probe", top_k=100))
        # Choose a non-leading candidate that is still in the scored prefix.
        scorer.selected = pool[3]["content"].split("\n", 1)[1]
        result = client.post("/search", json={"user_id": "u", "query": "candidate_probe", "top_k": 1}).json()["data"]
        assert len(result) == 1 and result[0]["content"] == pool[3]["content"]
        assert client.post("/search", json={"user_id": "other", "query": "candidate_probe", "top_k": 1}).json() == {"data": []}


def test_health_remains_available_while_model_thread_is_busy(tmp_path):
    async def run():
        entered, released = threading.Event(), threading.Event()
        class Scorer:
            def scores(self, query, documents):
                entered.set()
                assert released.wait(5)
                return [0] * len(documents)
        app = create_app(Settings(data_dir=tmp_path, api_key="test", max_concurrency=1), scorer=Scorer())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"X-Api-Key": "test"}) as client:
            assert (await client.post("/add", json={"user_id": "u", "request_id": "r", "session_id": "s",
                    "messages": [{"role": "tool", "content": "busy_model_probe source"}]})).status_code == 200
            task = asyncio.create_task(client.post("/search", json={"user_id": "u", "query": "busy_model_probe", "top_k": 1}))
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                assert (await asyncio.wait_for(client.get("/health"), .8)).status_code == 200
            finally:
                released.set()
                assert (await task).status_code == 200
    asyncio.run(run())


def test_selection_pool_keeps_useful_evidence_until_after_reranking(tmp_path):
    from heguang_code.schema import AddRequest
    from heguang_code.store import Store
    store = Store(Settings(data_dir=tmp_path, api_key="test", profile="rarity"))
    store.add(AddRequest(user_id="u", request_id="r", session_id="s", messages=[
        {"role": "tool", "content": f"poolneedle source {index:02}\n" + "original evidence context\n" * 100}
        for index in range(24)]))
    req = SearchRequest(user_id="u", query="poolneedle", top_k=100)
    legacy = store.search(req)
    pool = store.search(req, selection_pool=True)
    assert len(legacy) < 16 < len(pool)
    target = pool[13]
    assert target["id"] not in {row["id"] for row in legacy}
    scores = [100 if index == 13 else index for index in range(16)]
    selected = fit_budget(reorder(pool, scores), top_k=100, characters=32000)
    assert selected[0]["content"] == target["content"]
    assert sum(len(row["content"]) for row in selected) <= 32000
    assert all(row["content"] in {source["content"] for source in pool} for row in selected)
