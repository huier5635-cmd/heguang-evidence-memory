import json
import zlib

import pytest

from heguang_code.config import Settings
from heguang_code.feature_storage import MAGIC, MAX_JSON_BYTES, decode_features, encode_features
from heguang_code.schema import AddRequest, SearchRequest
from heguang_code.store import Store, digest


def test_small_and_large_unicode_feature_lists_are_lossless():
    for values in ([], ["path.py"], [f"\u03c3_project/component_{index}/source.py" for index in range(200)]):
        encoded = encode_features(values)
        assert decode_features(encoded) == sorted(values)
    assert isinstance(encode_features(["path.py"]), str)
    assert isinstance(encode_features([f"repeated_path_{index:04}.py" for index in range(200)]), bytes)


def test_corrupt_and_oversized_metadata_fails_closed():
    good = encode_features([f"repeated_symbol_{index:04}" for index in range(200)])
    # Declared-length overruns, truncated streams, concatenated streams, wrong
    # payload types and compressed expansion all reject without an unbounded decode.
    bomb = zlib.compress(b"x" * (MAX_JSON_BYTES + 1))
    invalid = [good[:-1], good + zlib.compress(b"[]"), b"wrongfmt", None,
               MAGIC + (MAX_JSON_BYTES + 1).to_bytes(4, "big") + bomb,
               MAGIC + (2).to_bytes(4, "big") + bomb, "{}", '[1]', '"symbols"']
    for value in invalid:
        with pytest.raises(RuntimeError, match="Corrupt derived"):
            decode_features(value)


def test_compressed_metadata_survives_restart_and_old_schema_is_rejected(tmp_path):
    settings = Settings(data_dir=tmp_path, api_key="test")
    source = "\n".join(f"def repeated_symbol_{index:04}(): return {index}" for index in range(150))
    store = Store(settings)
    request = AddRequest(request_id="r", user_id="u", session_id="s", messages=[{"role": "user", "content": source}])
    store.add(request)
    db = store.connect("u", create=False)
    try:
        assert any(isinstance(row[0], bytes) for row in db.execute("SELECT symbols FROM docs"))
    finally:
        db.close()
    restored = Store(settings)
    rows = restored.search(SearchRequest(user_id="u", query="repeated_symbol_0137", top_k=100))
    assert any("repeated_symbol_0137" in row["content"] for row in rows)
    restored.add(request)
    db = restored.connect("u")
    old_signature = digest({"schema": 4, "source_view": 4, "rich_tokens": True,
        "chunk_chars": 3600, "overlap_chars": 360, "block_chars": 8192})
    db.execute("UPDATE meta SET value=? WHERE key='signature'", (old_signature,))
    db.commit()
    db.close()
    with pytest.raises(RuntimeError, match="Configuration mismatch"):
        restored.search(SearchRequest(user_id="u", query="repeated_symbol_0137", top_k=100))
