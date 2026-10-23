"""Public development experiment: select/rerank before final evidence packing."""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from heguang_code.config import Settings
from heguang_code.reranker import fit_budget, reorder
from heguang_code.schema import SearchRequest
from heguang_code.store import Store, digest
from research.reranker import Reranker
from public_benchmark import load_public, render_history


def union_length(intervals):
    total, stop = 0, 0
    for start, end in sorted(intervals):
        total += max(0, end - max(start, stop))
        stop = max(stop, end)
    return total


def metrics(rows, gold, histories):
    offsets = {iid: render_history(histories[iid])[1] for iid in gold}
    lengths = {iid: len(histories[iid]["patch"] or "") for iid in gold}
    ranks, covered = [], {iid: [] for iid in gold}
    for rank, row in enumerate(rows, 1):
        prefix, _ = row["content"].split("\n", 1)
        provenance = json.loads(prefix[8:-1])
        iid = provenance["session_id"]
        if iid in gold:
            ranks.append(rank)
            start, end = provenance["characters"]
            begin = max(start, offsets[iid]) - offsets[iid]
            end = min(end - offsets[iid], lengths[iid])
            if end > begin:
                covered[iid].append((begin, end))
    first = min(ranks) if ranks else None
    amounts = {iid: union_length(value) for iid, value in covered.items()}
    denominator = sum(lengths.values())
    return {"first_positive_rank": first, "hit_at_1": first == 1,
            "hit_at_5": first is not None and first <= 5, "hit_at_20": first is not None and first <= 20,
            "reciprocal_rank": 1 / first if first else 0,
            "historical_patch_retrieved": any(value >= 32 for value in amounts.values()),
            "historical_patch_coverage": sum(amounts.values()) / denominator if denominator else 0,
            "any_complete_historical_patch": any(lengths[iid] and amounts[iid] == lengths[iid] for iid in gold),
            "returned_chunks": len(rows), "returned_characters": sum(len(row["content"]) for row in rows)}


def run(args):
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    if baseline["split"] != "dev" or baseline["profile"] != "rarity":
        raise RuntimeError("Selection experiments only use the public development split")
    targets, histories, _, relation, audit = load_public(args.data)
    store = Store(Settings(data_dir=args.index / "db", api_key="public-selection-only", profile="rarity"))
    scorer = Reranker(args.model_dir, args.cache, seconds=args.seconds)
    selected = [row for row in baseline["cases"] if row["noise_requested_per_positive"] == args.noise]
    cases, started, complete = [], time.perf_counter(), False
    source_hashes = {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in [*sorted((ROOT / "heguang_code").glob("*.py")), ROOT / "research/reranker.py", Path(__file__).resolve()]}
    try:
        for index, case in enumerate(selected):
            iid, query = case["target_id"], targets[case["target_id"]]["problem_statement"]
            user = "public-coding:" + digest([iid, args.noise])[:24]
            request = SearchRequest(user_id=user, query=query, top_k=100)
            old = store.search(request)
            assert metrics(old, relation[iid], histories)["first_positive_rank"] == case["first_positive_chunk_rank"]
            pool = store.search(request, selection_pool=True)
            old_scores = scorer.scores(query, [row["content"].split("\n", 1)[1] for row in old[:16]])
            new_scores = scorer.scores(query, [row["content"].split("\n", 1)[1] for row in pool[:16]])
            variants = {"lexical_32000": old, "legacy_rerank_32000": reorder(old, old_scores)}
            reordered = reorder(pool, new_scores)
            for budget in (32000, 64000, 128000):
                variants[f"select_then_pack_{budget}"] = fit_budget(reordered, top_k=100, characters=budget)
            for weight in (2, 4, 16):
                variants[f"legacy_weight{weight}_32000"] = reorder(old, old_scores, weight=weight)
                ranked = reorder(pool, new_scores, weight=weight)
                for budget in (32000, 64000, 128000):
                    variants[f"select_weight{weight}_pack_{budget}"] = fit_budget(ranked, top_k=100, characters=budget)
            measures = {name: metrics(rows, relation[iid], histories) for name, rows in variants.items()}
            cases.append({"target_id": iid, "repository": case["repository"], "candidate_pool_count": len(pool),
                          "candidate_pool_characters": sum(len(row["content"]) for row in pool), "variants": measures})
            if (index + 1) % 8 == 0:
                print(json.dumps({"event": "selection_case", "complete": index + 1, "total": len(selected),
                                  "elapsed_s": round(time.perf_counter() - started, 1)}), flush=True)
        complete = True
    finally:
        scorer.close()
        aggregates = {}
        if cases:
            for name in cases[0]["variants"]:
                aggregates[name] = {key: round(sum(case["variants"][name][key] for case in cases) / len(cases), 6)
                                    for key in cases[0]["variants"][name] if key != "first_positive_rank"}
        report = {"purpose": __doc__, "complete": complete, "split": "dev", "cases_completed": len(cases),
                  "noise_requested_per_positive": args.noise, "aggregate": aggregates, "cases": cases,
                  "source_sha256": source_hashes, "data_audit": audit,
                  "baseline_report_sha256": hashlib.sha256(args.baseline.read_bytes()).hexdigest(),
                  "observed_rss_bytes": scorer.observed_rss, "new_query_window_pairs": scorer.new_pairs,
                  "elapsed_seconds": round(time.perf_counter() - started, 3),
                  "official_submissions": 0, "paid_api_calls": 0,
                  "limitations": "Public development-only retrieval and historical-patch-coverage proxies, no current-target patch or tests read. Larger output budgets differ in context cost and can add noise; coding-task resolution remains unmeasured."}
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"complete": complete, "aggregate": aggregates}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--noise", type=int, choices=[16, 64], default=64)
    parser.add_argument("--seconds", type=int, default=420)
    run(parser.parse_args())
