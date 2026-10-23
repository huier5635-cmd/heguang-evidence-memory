"""Development-only cross-encoder reranking of the same fixed lexical evidence budget.

Not a dense recall benchmark, coding agent evaluation, official submission, or API.
"""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from heguang_code.config import Settings
from heguang_code.schema import SearchRequest
from heguang_code.store import Store, digest
from research.reranker import Reranker, MODEL_SHA256
from public_benchmark import load_public


def run(args):

    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    if baseline["split"] not in {"dev", "validation"} or baseline["profile"] != "rarity":
        raise RuntimeError("Only the public dev/validation splits are permitted; sealed_test is excluded")
    weights = (0, .25, .5, 1)
    plan_digest = None
    if baseline["split"] == "validation":
        if args.validation_plan is None:
            raise RuntimeError("Validation requires a predeclared research configuration")
        plan = json.loads(args.validation_plan.read_text(encoding="utf-8"))
        if plan["model_sha256"] != MODEL_SHA256 or plan["noise"] != args.noise:
            raise RuntimeError("Validation model/noise differs from the research plan")
        for name, expected in plan["source_sha256"].items():
            if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
                raise RuntimeError("Research source differs from predeclared validation plan")
        weights = (0, plan["weight"])
        plan_digest = hashlib.sha256(args.validation_plan.read_bytes()).hexdigest()
    for name, expected in baseline["source_sha256"].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != expected:
            raise RuntimeError("Lexical baseline does not match the current source")
    targets, _, _, relation, audit = load_public(args.data)
    settings = Settings(data_dir=args.index / "db", api_key="public-local-ablation-only", profile="rarity")
    store = Store(settings)
    selected = [row for row in baseline["cases"] if row["noise_requested_per_positive"] == args.noise]
    if not selected:
        raise RuntimeError("No cases match the requested noise level")
    encoder = Reranker(args.model_dir, args.cache, seconds=args.seconds, rss_mib=args.rss_mib)
    cases, started, complete = [], time.perf_counter(), False
    try:
        # Weights are declared here before viewing any semantic results.
        for index, case in enumerate(selected):
            iid = case["target_id"]
            target, gold = targets[iid], relation[iid]
            user = "public-coding:" + digest([iid, args.noise])[:24]
            rows = store.search(SearchRequest(user_id=user, query=target["problem_statement"], top_k=100))
            source_ids = [json.loads(row["content"].split("\n", 1)[0][8:-1])["session_id"] for row in rows]
            first = next((rank for rank, source_id in enumerate(source_ids, 1) if source_id in gold), None)
            if first != case["first_positive_chunk_rank"]:
                raise RuntimeError("Replayed lexical result differs from its baseline report")
            tick = time.perf_counter()
            similarities = encoder.scores(target["problem_statement"], [row["content"].split("\n", 1)[1] for row in rows])
            dense = sorted(range(len(rows)), key=lambda position: (-float(similarities[position]), rows[position]["id"]))
            dense_rank = {position: rank for rank, position in enumerate(dense, 1)}
            measures = {}
            for weight in weights:
                order = sorted(range(len(rows)), key=lambda position: (
                    -(1 / (60 + position + 1) + weight / (60 + dense_rank[position])), rows[position]["id"]))
                first = next((rank for rank, position in enumerate(order, 1) if source_ids[position] in gold), None)
                measures[str(weight)] = {"first_positive_chunk_rank": first,
                    "hit_at_1": first == 1, "hit_at_5": first is not None and first <= 5,
                    "reciprocal_rank": 1 / first if first else 0}
            cases.append({"target_id": iid, "repository": target["repo"], "returned_chunks": len(rows),
                          "retained_evidence_characters": sum(len(row["content"]) for row in rows),
                          "local_reranking_ms_including_cache": round((time.perf_counter() - tick) * 1000, 3),
                          "weights": measures})
            if (index + 1) % 8 == 0:
                print(json.dumps({"event": "public_semantic_case", "completed": index + 1, "total": len(selected),
                                  "elapsed_s": round(time.perf_counter() - started, 2)}), flush=True)
        complete = True
    finally:
        encoder.close()
        aggregates = {str(weight): {metric: round(sum(row["weights"][str(weight)][metric] for row in cases) / len(cases), 6)
                                  for metric in ("hit_at_1", "hit_at_5", "reciprocal_rank")}
                      for weight in weights} if cases else {}
        report = {"purpose": __doc__, "complete": complete, "split": baseline["split"], "cases_completed": len(cases),
                  "noise_requested_per_positive": args.noise, "validation_plan_sha256": plan_digest,
                  "model": "cross-encoder/ms-marco-MiniLM-L6-v2 quint8 AVX2 ONNX; direct query/passage scoring; max 512 tokens, 3 windows",
                  "model_sha256": MODEL_SHA256,
                  "baseline_report_sha256": hashlib.sha256(args.baseline.read_bytes()).hexdigest(),
                  "source_sha256": baseline["source_sha256"], "data_audit": audit,
                  "research_source_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in [Path(__file__).resolve(), ROOT / "research/reranker.py"]},
                  "aggregate": aggregates, "cases": cases, "elapsed_seconds": round(time.perf_counter() - started, 3),
                  "local_reranker_batches": encoder.batches, "new_query_window_pairs": encoder.new_pairs,
                  "queries_truncated_at_192_tokens": encoder.truncated_queries, "documents_with_multiple_windows": encoder.windowed_documents,
                  "observed_process_rss_bytes": encoder.observed_rss,
                  "guards": {"seconds": args.seconds, "rss_mib": args.rss_mib, "scope": "Before/after model calls; not an external hard kill"},
                  "external_api_calls": 0, "paid_api_calls": 0, "official_submissions": 0,
                  "limitations": "Fixed lexical candidates and identical returned text budget; reranking cannot repair missed candidates or missing patches. Small public repository splits, English cross-encoder, token truncation and bounded windows, local Windows CPU. No agent task resolution or production integration."}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "pass", "aggregate": aggregates, "observed_rss_bytes": encoder.observed_rss}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=240)
    parser.add_argument("--rss-mib", type=int, default=512)
    parser.add_argument("--noise", type=int, choices=[4, 16, 64], default=16)
    parser.add_argument("--validation-plan", type=Path)
    run(parser.parse_args())
