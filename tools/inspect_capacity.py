"""Read-only storage accounting on an already completed public fixture, with explicit scenarios."""
import argparse
import datetime
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inspect(args):
    evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
    assert evidence["worker_reaped"] and evidence["stop_reason"] == "completed_public_trajectory_sample"
    assert not list(args.db.glob("*.sqlite3-wal")), "Use a closed, checkpointed fixture only"
    files = sorted(args.db.glob("*.sqlite3"))
    assert files
    before = {path.name: (path.stat().st_size, sha(path)) for path in files}
    tables, counts = Counter(), Counter()
    for path in files:
        # immutable prevents journal/SHM creation. Never use this mode on a live database.
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True) as db:
            db.execute("PRAGMA query_only=ON")
            amount, chars = db.execute("SELECT count(*),coalesce(sum(chars),0) FROM sources").fetchone()
            counts.update({"messages": amount, "normalized_source_characters": chars})
            for name, size in db.execute("SELECT name,sum(pgsize) FROM dbstat GROUP BY name"):
                tables[name] += size
            counts["free_page_bytes"] += db.execute("PRAGMA freelist_count").fetchone()[0] * db.execute("PRAGMA page_size").fetchone()[0]
            counts["compressed_source_blob_bytes"] += db.execute("SELECT coalesce(sum(length(body)),0) FROM blobs").fetchone()[0]
            counts["compressed_structured_message_bytes"] += db.execute("SELECT coalesce(sum(length(body)),0) FROM raw_messages").fetchone()[0]
    after = {path.name: (path.stat().st_size, sha(path)) for path in files}
    assert before == after
    assert counts["messages"] == evidence["messages_restored_exactly"]
    assert counts["normalized_source_characters"] == evidence["normalized_source_view_characters"]
    allocated = sum(size for size, _ in before.values())
    assert allocated == evidence["database_bytes"]
    assert sum(tables.values()) + counts["free_page_bytes"] == allocated
    ratios = {"database_bytes_per_normalized_source_character": allocated / counts["normalized_source_characters"],
              "database_bytes_per_input_json_byte": allocated / evidence["input_json_bytes"]}
    # Neither denominator is known to match the organizer's approximate token total.
    scenarios = []
    for denominator, ratio in [("normalized_source_characters", ratios["database_bytes_per_normalized_source_character"]),
                               ("input_json_bytes", ratios["database_bytes_per_input_json_byte"])]:
        for units in (2, 4, 6):
            projected = args.reported_tokens * units * ratio
            provisioned = projected * 1.3 + 3 * 1024 ** 3
            scenarios.append({"assumed_token_denominator": denominator, "assumed_units_per_reported_token": units,
                "projected_database_bytes": round(projected), "with_30_percent_margin_and_3_gib_reserve_bytes": round(provisioned),
                "within_nominal_30_decimal_gb": provisioned <= 30_000_000_000})
    result = {"purpose": __doc__, "checked_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "source_sha256": {"tools/inspect_capacity.py": sha(Path(__file__))}, "evidence_sha256": sha(args.evidence),
        "public_trajectories": evidence["public_trajectories"], "isolated_user_databases": len(files),
        "database_bytes": allocated, "counts": dict(counts), "table_page_bytes": dict(tables.most_common()),
        "ratios": ratios, "scenarios": scenarios, "reported_full_tokens_used_for_scenarios": args.reported_tokens,
        "fixture_unchanged_by_inspection": True, "model_or_official_calls": 0, "full_allowed": False,
        "full_scale_capacity_verified": False,
        "conclusion": "Some explicit denominator scenarios exceed nominal capacity. Others fit. No unique forecast is justified without actual deployed headroom and the official token/input accounting.",
        "limitations": ["Different public benchmark, selected 120 historical trajectories, 56 repository-scoped users; not official CAMBench.",
            "Source view omits some raw metadata; structured JSON archives coexist with indexed source. Token count may refer to either or neither.",
            "Linear ratios cannot predict vocabulary growth, cross-user duplication, long-trajectory tails, WAL peaks, rebuild/VACUUM space or Linux throughput.",
            "30 decimal GB is nominal total disk, not measured free space. 30 percent margin and 3 GiB reserve are assumptions, not deployment evidence."]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as target:
        json.dump(result, target, ensure_ascii=False, indent=2)
        target.write("\n")
    print(json.dumps({"database_mib": round(allocated / 1024 ** 2, 2), "users": len(files), "messages": counts["messages"],
                      "fixture_unchanged": True, "full_capacity_verified": False, "scenario_gib_range_with_reserve":
                          [round(min(row["with_30_percent_margin_and_3_gib_reserve_bytes"] for row in scenarios) / 1024 ** 3, 2),
                           round(max(row["with_30_percent_margin_and_3_gib_reserve_bytes"] for row in scenarios) / 1024 ** 3, 2)]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("db", "evidence", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--reported-tokens", type=int, default=1_800_000_000)
    args = parser.parse_args()
    assert args.reported_tokens > 0
    inspect(args)
