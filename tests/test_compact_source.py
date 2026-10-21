import asyncio
import json

import pytest

from aml_memory.config import Settings
from aml_memory.engine import Engine
from aml_memory.schema import AddRequest, SearchRequest
from aml_memory.store import Store


def test_compact_text_and_media_keep_exact_sources_and_materialized_renderings(tmp_path):
    async def exercise():
        engine = Engine(Settings(data_dir=tmp_path, api_key="test"))
        source = '甲乙 "quoted" \\ path\nsecond line \u2603'
        parts = [{"type": "text", "text": "Photo caption telescope."},
                 {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGVsbG8="}}]
        req = AddRequest(request_id="r", user_id="u", session_id="s", messages=[
            {"role": "user", "timestamp": 1704067200000, "content": source},
            {"role": "assistant", "timestamp": 1704067201000, "content": parts}])
        expected = {doc["id"]: doc for doc in engine.documents(req)}
        try:
            await engine.add(req)
            db = engine.store.connect("u")
            rows = list(db.execute("SELECT * FROM docs ORDER BY doc_rowid"))
            assert len(rows) == 2 and rows[0]["text"] is None and rows[1]["text"] is not None
            stored_messages = list(db.execute("SELECT * FROM messages ORDER BY seq"))
            assert [json.loads(row["content"]) for row in stored_messages] == [source, parts]
            assert [row["timestamp"] for row in stored_messages] == [1704067200000, 1704067201000]
            assert all(row["text"] is None for row in stored_messages)
            assert [(row["role"], row["session_id"]) for row in stored_messages] == [("user", "s"), ("assistant", "s")]
            db.close()
            materialized = engine.store.fetch_docs("u", expected)
            assert all(row["text"] == expected[key]["text"] for key, row in materialized.items())
            assert all(json.loads(row["content"]) == expected[key]["content"] for key, row in materialized.items())
            assert "timestamp_ms" in materialized[rows[1]["id"]]["text"]
            # Empty lexical terms exercise the fallback path, not the FTS hit path.
            fallback, _ = engine.store.candidates("u", "the and or", [], 400)
            assert all(row["text"] == expected[key]["text"] for key, row in fallback.items())
            actual = await engine.search(SearchRequest(user_id="u", query="telescope quoted", top_k=100))
            assert {row["id"]: row["content"] for row in actual} == {key: doc["content"] for key, doc in expected.items()}
        finally:
            await engine.models.close()
    asyncio.run(exercise())


def test_distinct_document_rendering_is_not_dropped(tmp_path):
    store = Store(tmp_path, "test")
    req = AddRequest(request_id="r", user_id="u", session_id="s", messages=[{"role": "user", "content": "source"}])
    doc = {"id": "doc", "start": 0, "end": 1, "content": "original source", "text": "distinct source rendering", "index_text": "source"}
    store.commit(req, "digest", [doc])
    fetched = store.fetch_docs("u", ["doc"])["doc"]
    assert fetched["text"] == "distinct source rendering" and json.loads(fetched["content"]) == "original source"


@pytest.mark.parametrize("content", ['[1,2]', 'null', '{invalid'])
def test_missing_distinct_rendering_fails_instead_of_inventing_source(content):
    with pytest.raises(RuntimeError, match="source representation"):
        Store.materialize_doc({"id": "bad", "content": content, "text": None})
