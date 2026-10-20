import random

from aml_memory.schema import AddRequest
from aml_memory.store import Store, grams
from aml_memory.text import terms


def test_covering_lookup_preserves_bm25_id_ties_and_topk_boundaries(tmp_path):
    store = Store(tmp_path, "covering-test")
    shuffled = list(range(1000))
    random.Random(20261021).shuffle(shuffled)
    docs = [{"id": f"doc_{i:04}", "start": n, "end": n + 1,
             "content": "long source body " * 200, "text": "source " * 200,
             "index_text": "shared telescope notebook" if i % 3 else "shared telescope astronomy"}
            for n, i in enumerate(shuffled)]
    req = AddRequest(request_id="r", user_id="u", session_id="s",
                     messages=[{"role": "user", "content": "Synthetic index test."}])
    store.commit(req, "test", docs)
    db = store.connect("u")
    try:
        for table in ("words", "trigrams"):
            for query in ("shared telescope", "astronomy notebook", "missingquasar"):
                tokens = list(dict.fromkeys(terms(query, filter_stop=True) if table == "words" else grams(query)))[:160]
                expression = " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)
                for limit in (1, 5, 400):
                    expected = [row[0] for row in db.execute(
                        f"SELECT docs.id FROM {table} JOIN docs NOT INDEXED ON docs.rowid={table}.rowid "
                        f"WHERE {table} MATCH ? ORDER BY bm25({table}), docs.id LIMIT ?", (expression, limit))]
                    assert store.lexical(db, query, table, limit) == expected
                plan = [row[3] for row in db.execute(
                    f"EXPLAIN QUERY PLAN SELECT docs.id FROM {table} JOIN docs INDEXED BY docs_lookup ON docs.rowid={table}.rowid "
                    f"WHERE {table} MATCH ? ORDER BY bm25({table}),docs.id LIMIT 400", (expression,))]
                assert any("COVERING INDEX docs_lookup" in item for item in plan)
    finally:
        db.close()
