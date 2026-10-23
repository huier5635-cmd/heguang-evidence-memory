"""Compare current bounded production codec with frozen dev3 over copied public data."""
import argparse
import hashlib
import importlib
import importlib.util
import json
import shutil
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from heguang_code.config import Settings
from heguang_code.feature_storage import encode_features, decode_features
from heguang_code.schema import SearchRequest
from heguang_code.store import Store, digest


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run(args):
    assert not list(args.db.glob("*.sqlite3-wal"))
    legacy_root = args.legacy / "heguang_code"
    spec = importlib.util.spec_from_file_location("frozen_dev3", legacy_root / "__init__.py", submodule_search_locations=[str(legacy_root)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    assert module.__version__ == "0.2.0.dev3"
    # Git on Windows may check out CRLF; validate the canonical LF source.
    canonical = (legacy_root / "store.py").read_text(encoding="utf-8").encode()
    assert hashlib.sha256(canonical).hexdigest() == "13cbed7c2f812cd2235b1f2e8d54ab50eb3e2bee5a9e775a7248f5b796c17699"
    OldStore = importlib.import_module("frozen_dev3.store").Store
    original = OldStore(Settings(data_dir=args.db, api_key="public-read-only"))
    args.work.mkdir(parents=True, exist_ok=False)
    current = Store(Settings(data_dir=args.work, api_key="public-read-only"))
    source_files = sorted(args.db.glob("*.sqlite3"))
    hashes = {path.name: sha(path) for path in source_files}
    fields = 0
    for path in source_files:
        target = args.work / path.name
        shutil.copyfile(path, target)
        with closing(sqlite3.connect(target)) as db:
            with db:
                for rowid, paths, symbols in db.execute("SELECT rowid,paths,symbols FROM docs").fetchall():
                    converted = [encode_features(json.loads(value)) for value in (paths, symbols)]
                    assert all(decode_features(new) == json.loads(old) for old, new in zip((paths, symbols), converted, strict=True))
                    db.execute("UPDATE docs SET paths=?,symbols=? WHERE rowid=?", (*converted, rowid))
                    fields += 2
                db.execute("UPDATE meta SET value=? WHERE key='signature'", (current.signature,))
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            db.execute("VACUUM")
    receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
    users = sorted({"public-trajectory:" + digest(row["path"].split("/")[2])[:24] for row in receipt["files"]})
    cases = 0
    for user in users:
        db = original.connect(user, create=False)
        try:
            queries = {"historical implementation"}
            for paths, symbols in db.execute("SELECT paths,symbols FROM docs ORDER BY length(paths)+length(symbols) DESC LIMIT 4"):
                queries.update([*json.loads(paths)[:1], *json.loads(symbols)[:1]])
            for query in sorted(queries)[:4]:
                request = SearchRequest(user_id=user, query=query, top_k=100)
                for pool in (False, True):
                    assert original.search(request, selection_pool=pool) == current.search(request, selection_pool=pool)
                    cases += 1
        finally:
            db.close()
    assert hashes == {path.name: sha(path) for path in source_files}
    assert cases == 432 and not list(args.work.glob("*.sqlite3-wal"))
    report = {"purpose": __doc__, "passed": True, "retrieval_cases_equal": cases, "feature_fields_verified": fields,
        "original_fixture_unchanged": True, "users": len(users),
        "closed_database_bytes": sum(path.stat().st_size for path in args.work.glob("*.sqlite3")),
        "legacy_commit": "56c9d08c42dc7ac0ef0796459dcff690b5b018e4", "receipt_sha256": sha(args.receipt),
        "source_sha256": {path.relative_to(ROOT).as_posix(): sha(path) for path in sorted((ROOT / "heguang_code").glob("*.py"))},
        "probe_source_sha256": sha(Path(__file__)), "official_calls": 0, "full_allowed": False,
        "limitations": "Storage and candidate-output equivalence on one public fixture; no new relevance, task-resolution, deployed capacity or Full score claim. Test-only copies are transformed; production never migrates an old index automatically."}
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(report, output, indent=2)
        output.write("\n")
    print(json.dumps({key: report[key] for key in ("passed", "retrieval_cases_equal", "feature_fields_verified", "closed_database_bytes")}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("db", "legacy", "receipt", "work", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    run(parser.parse_args())
