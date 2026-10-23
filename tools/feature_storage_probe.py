"""Compress feature metadata in COPIES of public fixture databases; check retrieval parity."""
import argparse
from contextlib import closing
import hashlib
import json
import shutil
import sqlite3
import sys
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from heguang_code.config import Settings
from heguang_code.schema import SearchRequest
from heguang_code.store import Store, digest


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def compact(value):
    raw = value.encode()
    if len(raw) < 96:
        return value
    packed = zlib.compress(raw, 3)
    return packed if len(packed) + 8 < len(raw) else value


def unpack(value):
    return zlib.decompress(value).decode() if isinstance(value, bytes) else value


class PrototypeStore(Store):
    def connect(self, user, create=True):
        assert not create, "Prototype never accepts writes"
        db = super().connect(user, create=False)
        if db is not None:
            def decode(cursor, values):
                converted = tuple(unpack(value) if column[0] in {"paths", "symbols"} else value
                                  for column, value in zip(cursor.description, values, strict=True))
                return sqlite3.Row(cursor, converted)
            db.row_factory = decode
        return db


def run(args):
    started = time.perf_counter()
    assert not list(args.db.glob("*.sqlite3-wal"))
    receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
    users = sorted({"public-trajectory:" + digest(row["path"].split("/")[2])[:24] for row in receipt["files"]})
    args.work.mkdir(parents=True, exist_ok=False)
    original = Store(Settings(data_dir=args.db, api_key="public-read-only-probe"))
    prototype = PrototypeStore(Settings(data_dir=args.work, api_key="public-read-only-probe"))
    sources = sorted(args.db.glob("*.sqlite3"))
    assert len(sources) == len(users) and sum(path.stat().st_size for path in sources) < 128 * 1024 ** 2
    before_hashes = {path.name: sha(path) for path in sources}
    old_bytes = sum(path.stat().st_size for path in sources)
    original_fields, packed_fields, changed, rows_checked, cases = 0, 0, 0, 0, []
    for source in sources:
        target = args.work / source.name
        shutil.copyfile(source, target)
        with closing(sqlite3.connect(target)) as db:
            with db:
                for rowid, paths, symbols in db.execute("SELECT rowid,paths,symbols FROM docs").fetchall():
                    encoded = [compact(paths), compact(symbols)]
                    for old, new in zip((paths, symbols), encoded, strict=True):
                        assert unpack(new) == old and json.loads(unpack(new)) == json.loads(old)
                        original_fields += len(old.encode())
                        packed_fields += len(new if isinstance(new, bytes) else new.encode())
                        changed += isinstance(new, bytes)
                    db.execute("UPDATE docs SET paths=?,symbols=? WHERE rowid=?", (*encoded, rowid))
                    rows_checked += 1
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            db.execute("VACUUM")
            assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        if time.perf_counter() - started > 120:
            raise RuntimeError("Public prototype time guard")
    for user in users:
        db = original.connect(user, create=False)
        assert db is not None
        try:
            feature_rows = db.execute("SELECT paths,symbols FROM docs ORDER BY length(paths)+length(symbols) DESC LIMIT 4").fetchall()
            queries = {"historical implementation"}
            for paths, symbols in feature_rows:
                for value in [*json.loads(paths)[:1], *json.loads(symbols)[:1]]:
                    queries.add(value)
            for query in sorted(queries)[:4]:
                req = SearchRequest(user_id=user, query=query, top_k=100)
                for pool in (False, True):
                    before = original.search(req, selection_pool=pool)
                    after = prototype.search(req, selection_pool=pool)
                    assert before == after
                    cases.append({"query_sha256": hashlib.sha256(query.encode()).hexdigest(), "selection_pool": pool,
                                  "records": len(before), "equal": True})
        finally:
            db.close()
        if time.perf_counter() - started > 120:
            raise RuntimeError("Public prototype time guard")
    after_hashes = {path.name: sha(path) for path in sources}
    assert before_hashes == after_hashes
    assert not list(args.work.glob("*.sqlite3-wal"))
    new_bytes = sum(path.stat().st_size for path in args.work.glob("*.sqlite3"))
    report = {"purpose": __doc__, "production_code_changed": False, "production_deployed": False,
        "source_sha256": {"tools/feature_storage_probe.py": sha(Path(__file__)), "heguang_code/store.py": sha(ROOT / "heguang_code/store.py")},
        "receipt_sha256": sha(args.receipt), "public_trajectories": len(receipt["files"]), "users": len(users),
        "size_measurement": "Closed, checkpointed .sqlite3 files only, no live WAL/SHM; not peak disk usage",
        "original_fixture_unchanged": True, "original_database_bytes": old_bytes, "prototype_database_bytes": new_bytes,
        "saved_fraction": round(1 - new_bytes / old_bytes, 6), "document_rows_verified": rows_checked,
        "original_field_bytes": original_fields, "prototype_field_bytes": packed_fields, "compressed_fields": changed,
        "retrieval_cases_equal": len(cases), "retrieval_cases": cases, "elapsed_seconds": round(time.perf_counter() - started, 3),
        "codec": "UTF-8 JSON; zlib level 3 only for >=96 bytes and >8 bytes saving; small fields remain text",
        "quality_tuned": False, "official_score": False, "model_calls": 0, "full_allowed": False,
        "limitations": "Public, closed fixture only. Prototype uses a decoding row factory, not production schema/code. Same query results do not prove new task-resolution gains, full capacity, Linux performance or safe production codec limits. Integration requires bounded decoding, new index signature and new tests."}
    with args.output.open("x", encoding="utf-8") as target:
        json.dump(report, target, indent=2)
        target.write("\n")
    print(json.dumps({key: report[key] for key in ("original_database_bytes", "prototype_database_bytes", "saved_fraction", "retrieval_cases_equal", "production_code_changed")}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("db", "receipt", "work", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    run(parser.parse_args())
