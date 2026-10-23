import json
import sqlite3
from dataclasses import replace

import pytest

from heguang_code.config import Settings
from heguang_code.schema import AddRequest, SearchRequest
from heguang_code.store import ConflictingWrite, Store


def request(text, user="u", rid="r", session="s"):
    return AddRequest(user_id=user, request_id=rid, session_id=session,
                      messages=[{"role": "tool", "content": text, "timestamp": 1234567890000}])


def test_exact_unicode_blocks_spans_and_reopen(tmp_path):
    settings = Settings(data_dir=tmp_path, api_key="test", chunk_chars=700, overlap_chars=100, block_chars=1024)
    store = Store(settings)
    source = ('# source file café/请求.py\n\\quoted "雪"\r\n' * 120) + '\nTAIL_SYMBOL_7824()'
    req = request(source, user="u/../独立")
    store.add(req)
    store = Store(settings)
    db = store.connect(req.user_id, create=False)
    row = db.execute("SELECT * FROM sources").fetchone()
    assert row["role"] == "tool" and row["timestamp"] == 1234567890000
    assert store.source_span(db, row["id"], 0, len(source), {}) == source
    assert store.source_span(db, row["id"], 970, 2100, {}) == source[970:2100]
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    db.close()
    results = store.search(SearchRequest(user_id=req.user_id, query="TAIL_SYMBOL_7824", top_k=100))
    assert any("TAIL_SYMBOL_7824" in row["content"] for row in results)
    for row in results:
        prefix, raw = row["content"].split("\n", 1)
        provenance = json.loads(prefix.removeprefix("[source ").removesuffix("]"))
        start, end = provenance["characters"]
        assert source[start:end] == raw
    assert not store.search(SearchRequest(user_id="another-user", query="TAIL_SYMBOL_7824", top_k=100))


def test_deduplication_keeps_each_source_and_idempotency(tmp_path):
    store = Store(Settings(data_dir=tmp_path, api_key="test"))
    source = "src/parser.py parse_packet() handles missing data without changing parser state."
    first = request(source)
    store.add(first)
    before = store.search(SearchRequest(user_id="u", query="parse_packet", top_k=100))
    store.add(first)
    assert before == store.search(SearchRequest(user_id="u", query="parse_packet", top_k=100))
    store.add(request(source, rid="r2", session="other"))
    db = store.connect("u", create=False)
    assert db.execute("SELECT count(*) FROM sources").fetchone()[0] == 2
    assert db.execute("SELECT count(*) FROM blobs").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM docs").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM spans").fetchone()[0] == 2
    db.close()
    with pytest.raises(ConflictingWrite):
        store.add(request("different content"))


def test_partial_fts_failure_rolls_back_all_source_data(tmp_path):
    store = Store(Settings(data_dir=tmp_path, api_key="test"))
    original = store.connect
    def denied(user, create=True):
        db = original(user, create)
        db.set_authorizer(lambda action, table, *rest: sqlite3.SQLITE_DENY
                          if action == sqlite3.SQLITE_INSERT and table == "search" else sqlite3.SQLITE_OK)
        return db
    store.connect = denied
    with pytest.raises(sqlite3.DatabaseError):
        store.add(request("rollback_probe_symbol() is never committed"))
    store.connect = original
    db = store.connect("u", create=False)
    for table in ("sources", "blobs", "blocks", "docs", "spans", "writes"):
        assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    db.close()


def test_mixed_age_retention_keeps_shared_blocks_and_retry(tmp_path):
    store = Store(Settings(data_dir=tmp_path, api_key="test"))
    store.add(request("shared_file.py common_symbol()", rid="old"))
    fresh = request("shared_file.py common_symbol()", rid="fresh")
    store.add(fresh)
    db = store.connect("u")
    for table in ("sources", "writes"):
        db.execute(f"UPDATE {table} SET added_at=0 WHERE request_id='old'")
    db.commit()
    db.close()
    store.purge_expired()
    store.add(fresh)
    assert store.search(SearchRequest(user_id="u", query="common_symbol", top_k=100))
    db = store.connect("u", create=False)
    assert db.execute("SELECT count(*) FROM sources").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM docs").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM blobs").fetchone()[0] == 1
    db.close()


def test_corruption_fails_without_fabricating_content(tmp_path):
    store = Store(Settings(data_dir=tmp_path, api_key="test"))
    store.add(request("corrupt_symbol() original evidence"))
    db = store.connect("u")
    db.execute("UPDATE blobs SET body=?", (b"not-compressed",))
    db.commit()
    db.close()
    with pytest.raises(RuntimeError, match="Corrupt source"):
        store.search(SearchRequest(user_id="u", query="corrupt_symbol", top_k=100))


def test_index_configuration_cannot_change_in_place(tmp_path):
    settings = Settings(data_dir=tmp_path, api_key="test")
    Store(settings).add(request("configuration_guard()"))
    with pytest.raises(RuntimeError, match="Configuration mismatch"):
        Store(replace(settings, profile="baseline")).search(SearchRequest(user_id="u", query="configuration_guard", top_k=100))


def test_session_diversity_preserves_evidence_and_nonincreasing_scores(tmp_path):
    settings = Settings(data_dir=tmp_path, api_key="test", profile="rarity")
    store = Store(settings)
    sources = {}
    for index in range(8):
        content = f"diversityneedle original evidence {index:02}"
        sources[str(index)] = content
        store.add(request(content, rid=str(index), session="long-run" if index < 7 else "other-run"))
    # A search-only change can reuse the very same source/index representation.
    varied = Store(replace(settings, session_penalty=1.0))
    rows = varied.search(SearchRequest(user_id="u", query="diversityneedle", top_k=100))
    assert len(rows) == 8
    assert all(rows[i]["score"] >= rows[i + 1]["score"] for i in range(7))
    provenance = [json.loads(row["content"].split("\n", 1)[0][8:-1]) for row in rows]
    assert len({item["session_id"] for item in provenance[:2]}) == 2
    assert {row["content"].split("\n", 1)[1] for row in rows} == set(sources.values())
