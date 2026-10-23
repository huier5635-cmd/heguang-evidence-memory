"""Vacuum-only control for the frozen metadata-compression prototype; original fixture read only."""
import argparse
from contextlib import closing
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run(args):
    prior = json.loads(args.prototype.read_text(encoding="utf-8"))
    assert prior["original_fixture_unchanged"] and prior["retrieval_cases_equal"] == 432
    assert not list(args.db.glob("*.sqlite3-wal"))
    sources = sorted(args.db.glob("*.sqlite3"))
    before = {path.name: sha(path) for path in sources}
    assert sum(path.stat().st_size for path in sources) == prior["original_database_bytes"]
    args.work.mkdir(parents=True, exist_ok=False)
    for path in sources:
        target = args.work / path.name
        shutil.copyfile(path, target)
        with closing(sqlite3.connect(target)) as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            db.execute("VACUUM")
            assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    assert not list(args.work.glob("*.sqlite3-wal"))
    control_bytes = sum(path.stat().st_size for path in args.work.glob("*.sqlite3"))
    assert before == {path.name: sha(path) for path in sources}
    report = {"purpose": __doc__, "source_sha256": {"tools/feature_storage_control.py": sha(Path(__file__))},
        "prototype_report_sha256": sha(args.prototype), "original_fixture_unchanged": True,
        "size_measurement": "Closed, checkpointed .sqlite3 files only, no live WAL/SHM; not peak disk usage",
        "original_database_bytes": prior["original_database_bytes"], "vacuum_only_database_bytes": control_bytes,
        "compressed_and_vacuumed_database_bytes": prior["prototype_database_bytes"],
        "additional_saved_fraction_vs_vacuum_control": round(1 - prior["prototype_database_bytes"] / control_bytes, 6),
        "production_code_changed": False, "official_or_model_calls": 0, "full_allowed": False,
        "limitation": "One selected public fixture. Controls page compaction only; production codec, runtime and larger-scale tests remain necessary."}
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(report, output, indent=2)
        output.write("\n")
    print(json.dumps(report))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("db", "prototype", "work", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    run(parser.parse_args())
