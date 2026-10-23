"""Public retrieval proxy only. No official endpoints, keys, agents or model calls.

Target patches/tests are never loaded. Historical patches are source memories.
Author relationship annotations stay in this evaluator, outside the memory API.
"""
import argparse
import hashlib
import json
import random
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from heguang_code.config import Settings
from heguang_code.schema import AddRequest, SearchRequest
from heguang_code.store import Store, digest


def split_for(repo):
    bucket = int(hashlib.sha256(("coding-20261023:" + repo).encode()).hexdigest()[:8], 16) % 100
    return "dev" if bucket < 60 else "validation" if bucket < 80 else "sealed_test"


def issue_number(iid):
    return int(iid.rsplit("-", 1)[1])


def load_public(root):
    folder = root / "SWEContextBench/data"
    history_columns = ["instance_id", "repo", "problem_statement", "hints_text", "patch", "created_at"]
    # Explicit column whitelist prevents reading any target solution or target test.
    target_columns = ["instance_id", "repo", "problem_statement", "created_at"]
    histories = pq.read_table(folder / "SWEContextBench_Experience.parquet", columns=history_columns).to_pylist()
    target_rows = pq.read_table(folder / "SWEContextBench_Related.parquet", columns=target_columns).to_pylist()
    links = pq.read_table(folder / "SWEContextBench_Relationship.parquet",
                          columns=["related_instance_id", "experience_instance_id"]).to_pylist()
    grouped = defaultdict(list)
    for row in target_rows:
        grouped[row["instance_id"]].append(row)
    ambiguous = {iid for iid, rows in grouped.items() if len({digest(row) for row in rows}) > 1}
    targets = {iid: rows[0] for iid, rows in grouped.items() if iid not in ambiguous}
    history = {row["instance_id"]: row for row in histories}
    relation = defaultdict(set)
    invalid = Counter()
    for link in links:
        target = targets.get(link["related_instance_id"])
        earlier = history.get(link["experience_instance_id"])
        if target is None or earlier is None:
            invalid["missing_or_ambiguous"] += 1
        elif target["repo"] != earlier["repo"]:
            invalid["different_repository"] += 1
        elif target["instance_id"] == earlier["instance_id"]:
            invalid["self_history"] += 1
        elif issue_number(earlier["instance_id"]) >= issue_number(target["instance_id"]):
            invalid["not_lower_pr_number"] += 1
        elif not earlier["created_at"] or not target["created_at"] or earlier["created_at"] >= target["created_at"]:
            invalid["not_strictly_earlier"] += 1
        else:
            relation[target["instance_id"]].add(earlier["instance_id"])
    by_repo = defaultdict(list)
    for row in histories:
        by_repo[row["repo"]].append(row)
    report = {"history_rows": len(histories), "target_rows": len(target_rows),
              "unique_target_ids": len(grouped), "ambiguous_target_ids_excluded": sorted(ambiguous),
              "relationships": len(links), "excluded_relationships": dict(invalid),
              "eligible_queries": len(relation), "split_seed": "coding-20261023",
              "split_unit": "repository", "target_fields_loaded": target_columns,
              "history_fields_loaded": history_columns,
              "eligible_split_counts": dict(Counter(split_for(targets[iid]["repo"]) for iid in relation)),
              "source_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(folder.glob("*.parquet"))}}
    return targets, history, by_repo, relation, report


def render_history(row):
    source = ("Historical task " + row["instance_id"] + "\nRepository: " + row["repo"]
              + "\n\nIssue statement:\n" + row["problem_statement"]
              + "\n\nContemporaneous discussion:\n" + (row["hints_text"] or "")
              + "\n\nHistorical implementation diff:\n")
    return source + (row["patch"] or ""), len(source)


def percentile(values, p):
    return sorted(values)[int((len(values) - 1) * p)] if values else None


def run(args):
    targets, history, by_repo, relation, data_audit = load_public(args.data)
    if args.split == "sealed_test" and not args.frozen_candidate:
        raise RuntimeError("A sealed-test run requires a frozen candidate manifest")
    if args.frozen_candidate:
        frozen = json.loads(args.frozen_candidate.read_text())
        for filename, expected in frozen["source_sha256"].items():
            if hashlib.sha256((ROOT / filename).read_bytes()).hexdigest() != expected:
                raise RuntimeError("Frozen candidate does not match source")
    args.work.mkdir(parents=True, exist_ok=False)
    settings = Settings(data_dir=args.work / "db", api_key="public-proxy-test", profile=args.profile,
                        chunk_chars=args.chunk_chars, overlap_chars=args.overlap_chars,
                        response_chars=args.response_chars, session_penalty=args.session_penalty)
    store = Store(settings)
    selected = sorted((iid for iid in relation if split_for(targets[iid]["repo"]) == args.split),
                      key=lambda iid: hashlib.sha256(("order:" + iid).encode()).hexdigest())
    if args.limit:
        selected = selected[:args.limit]
    started, cases = time.perf_counter(), []
    for index, iid in enumerate(selected):
        target = targets[iid]
        gold = relation[iid]
        negatives = sorted((row for row in by_repo[target["repo"]]
                            if row["instance_id"] not in gold and row["instance_id"] != iid
                            and issue_number(row["instance_id"]) < issue_number(iid)
                            and row["created_at"] and row["created_at"] < target["created_at"]),
                           key=lambda row: row["instance_id"])
        rng = random.Random(int(hashlib.sha256(("noise:" + iid).encode()).hexdigest(), 16))
        rng.shuffle(negatives)
        for noise in args.noise:
            chosen = [history[key] for key in sorted(gold)] + negatives[:min(len(negatives), noise * len(gold))]
            rng_case = random.Random(int(hashlib.sha256(("case:" + iid + ":" + str(noise)).encode()).hexdigest(), 16))
            rng_case.shuffle(chosen)
            user = "public-coding:" + digest([iid, noise])[:24]
            patch_offsets = {}
            raw_chars, added = 0, time.perf_counter()
            for row in chosen:
                source, patch_start = render_history(row)
                patch_offsets[row["instance_id"]] = patch_start
                raw_chars += len(source)
                store.add(AddRequest(user_id=user, request_id=row["instance_id"], session_id=row["instance_id"],
                                     messages=[{"role": "user", "content": source}]))
            add_ms = (time.perf_counter() - added) * 1000
            queried = time.perf_counter()
            rows = store.search(SearchRequest(user_id=user, query=target["problem_statement"], top_k=100))
            search_ms = (time.perf_counter() - queried) * 1000
            matches, patch_match = [], False
            for rank, row in enumerate(rows, 1):
                prefix, raw = row["content"].split("\n", 1)
                provenance = json.loads(prefix.removeprefix("[source ").removesuffix("]"))
                history_id = provenance["session_id"]
                if history_id in gold:
                    matches.append(rank)
                    begin, end = provenance["characters"]
                    patch_match |= end - max(begin, patch_offsets[history_id]) >= 32
            first = min(matches) if matches else None
            db = store.connect(user, create=False)
            assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            counts = {table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in ("sources", "docs", "blobs")}
            db.close()
            record = {"target_id": iid, "repository": target["repo"], "split": args.split,
                      "noise_requested_per_positive": noise, "history_count": len(chosen), "positive_count": len(gold),
                      "negative_count": len(chosen) - len(gold), "first_positive_chunk_rank": first,
                      "hit_at_1": first == 1, "hit_at_5": first is not None and first <= 5,
                      "hit_at_20": first is not None and first <= 20,
                      "historical_patch_retrieved": patch_match,
                      "reciprocal_rank": 1 / first if first else 0,
                      "returned_chunks": len(rows), "returned_chars": sum(len(row["content"]) for row in rows),
                      "source_chars": raw_chars, "database_bytes": store.path(user).stat().st_size,
                      "add_ms": round(add_ms, 3), "search_ms": round(search_ms, 3), "counts": counts}
            cases.append(record)
        print(json.dumps({"event": "target_complete", "targets": index + 1, "of": len(selected),
                          "cases": len(cases), "elapsed_s": round(time.perf_counter() - started, 1)}), flush=True)
    groups = {}
    for noise in args.noise:
        sample = [row for row in cases if row["noise_requested_per_positive"] == noise]
        if sample:
            groups[str(noise)] = {"cases": len(sample), **{metric: round(sum(row[metric] for row in sample) / len(sample), 6)
                for metric in ("hit_at_1", "hit_at_5", "hit_at_20", "historical_patch_retrieved", "reciprocal_rank")},
                "search_p95_ms": round(percentile([row["search_ms"] for row in sample], 0.95), 3),
                "database_bytes": sum(row["database_bytes"] for row in sample),
                "source_chars": sum(row["source_chars"] for row in sample)}
    report = {"purpose": "Public SWEContextBench causal same-repository relationship retrieval proxy",
              "official_score": False, "official_submissions": 0, "real_model_calls": 0,
              "profile": args.profile, "split": args.split, "target_limit": args.limit,
              "settings": {key: value for key, value in asdict(settings).items() if key not in {"api_key", "data_dir"}},
              "data_audit": data_audit,
              "source_sha256": {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                                for path in sorted((ROOT / "heguang_code").glob("*.py"))},
              "aggregate": groups, "cases": cases, "elapsed_seconds": round(time.perf_counter() - started, 3),
              "limitations": "Author relationship labels are incomplete relevance proxies. Strict earlier-date filtering changes the original benchmark. This run does not execute a coding agent or measure resolved tasks and is not official CAMBench. Repository splits are fixed; dev/validation may guide tuning, sealed_test may only be used after freezing."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "pass", "split": args.split, "profile": args.profile, "aggregate": groups}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", choices=["baseline", "code", "rarity"], required=True)
    parser.add_argument("--split", choices=["dev", "validation", "sealed_test"], default="dev")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--noise", type=int, nargs="+", default=[0, 4, 16])
    parser.add_argument("--chunk-chars", type=int, default=3600)
    parser.add_argument("--overlap-chars", type=int, default=360)
    parser.add_argument("--response-chars", type=int, default=32000)
    parser.add_argument("--session-penalty", type=float, default=0)
    parser.add_argument("--frozen-candidate", type=Path)
    run(parser.parse_args())
