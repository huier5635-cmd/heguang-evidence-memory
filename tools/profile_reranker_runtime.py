"""Offline CPU runtime ablation on previously used public development inputs.

No official requests, new quality claim, production edits, or persistent score cache.
Every runtime has its own process so resident models do not overlap.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

VARIANTS = {
    "basic_t2": {"threads": 2, "optimization": "ORT_ENABLE_BASIC", "spinning": True},
    "basic_t1": {"threads": 1, "optimization": "ORT_ENABLE_BASIC", "spinning": False},
    "basic_t2_no_spin": {"threads": 2, "optimization": "ORT_ENABLE_BASIC", "spinning": False},
    "all_t1": {"threads": 1, "optimization": "ORT_ENABLE_ALL", "spinning": False},
    "all_t2_no_spin": {"threads": 2, "optimization": "ORT_ENABLE_ALL", "spinning": False},
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def create_fixture(args):
    from public_benchmark import issue_number, load_public, render_history, split_for
    targets, histories, by_repo, relation, audit = load_public(args.data)
    reference = json.loads((ROOT / "results/dev6-reranker-port.json").read_text(encoding="utf-8"))
    cases = []
    for old in reference["cases"]:
        iid = old["target_id"]
        target = targets[iid]
        assert split_for(target["repo"]) == "dev", "Never open sealed runtime cases"
        positives = sorted(relation[iid], key=lambda value: hashlib.sha256(value.encode()).hexdigest())[:2]
        earlier = [row["instance_id"] for row in by_repo[target["repo"]]
                   if row["created_at"] and row["created_at"] < target["created_at"]
                   and issue_number(row["instance_id"]) < issue_number(iid)
                   and row["instance_id"] not in positives]
        selected = positives + sorted(earlier, key=lambda value: hashlib.sha256(value.encode()).hexdigest())[:2]
        # Same original prefix limit as the deployed scorer; no target answer/test.
        docs = [render_history(histories[value])[0][:12000] for value in selected]
        assert docs
        cases.append({"id": iid, "query": target["problem_statement"], "documents": docs,
                      "document_ids": selected})
    cases.extend([
        {"id": "synthetic_short", "query": "retry exceptions", "documents": ["", "Preserve retry exceptions", "Return a cached value."]},
        {"id": "synthetic_long_query", "query": ("parse_unicode_identifier " * 500) + " preserve failure", "documents": ["Unicode identifiers preserve failure.\n" * 320, "Unrelated worker pool.\n" * 320]},
    ])
    out = {"purpose": __doc__, "official_data": False, "new_blind_quality_test": False,
           "source": audit["source_sha256"], "public_target_columns": audit["target_fields_loaded"],
           "selection": "Eight already used development queries from dev6 port verification plus two synthetic cases; at most four causal history originals per query", "cases": cases}
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "fixture.json").write_text(json.dumps(out, ensure_ascii=False) + "\n", encoding="utf-8")


def worker(args):
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    import numpy as np
    import onnxruntime as ort
    import psutil
    from heguang_code.reranker import PairScorer, MODEL_SHA256
    config = VARIANTS[args.worker]
    original_session = ort.InferenceSession

    def configured_session(*pos, **kwargs):
        options = kwargs["sess_options"]
        options.intra_op_num_threads = config["threads"]
        options.graph_optimization_level = getattr(ort.GraphOptimizationLevel, config["optimization"])
        options.add_session_config_entry("session.intra_op.allow_spinning", "1" if config["spinning"] else "0")
        options.add_session_config_entry("session.inter_op.allow_spinning", "1" if config["spinning"] else "0")
        return original_session(*pos, **kwargs)

    ort.InferenceSession = configured_session
    fixture_path = args.output / "fixture.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    process = psutil.Process()
    original_affinity = process.cpu_affinity()
    if args.one_core:
        process.cpu_affinity([original_affinity[0]])
    if os.name == "nt":
        process.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    stop = threading.Event()
    memory = {"rss_peak_sampled_bytes": 0}

    def monitor():
        while not stop.is_set():
            memory["rss_peak_sampled_bytes"] = max(memory["rss_peak_sampled_bytes"], process.memory_info().rss)
            stop.wait(.025)

    watcher = threading.Thread(target=monitor, daemon=True)
    watcher.start()
    start = time.perf_counter()
    scorer = PairScorer(args.model_dir)
    load_seconds = time.perf_counter() - start
    measurements = []
    cpu_start = time.process_time()
    try:
        for repeat in range(2):
            for case in fixture["cases"]:
                begin = time.perf_counter()
                scores = scorer.scores(case["query"], case["documents"])
                assert len(scores) == len(case["documents"]) and all(np.isfinite(scores))
                measurements.append({"case_id": case["id"], "repeat": repeat,
                                     "seconds": time.perf_counter() - begin, "scores": scores,
                                     "score_order": sorted(range(len(scores)), key=lambda index: (-scores[index], index))})
    finally:
        stop.set()
        watcher.join()
    report = {"variant": args.worker, "configuration": config, "completed": True,
              "fixture_sha256": sha(fixture_path), "model_sha256": MODEL_SHA256,
              "source_sha256": {"scorer": sha(ROOT / "heguang_code/reranker.py"), "tool": sha(Path(__file__))},
              "python": sys.version, "onnxruntime": ort.__version__, "cpu_cores_visible": os.cpu_count(),
              "cpu_affinity": process.cpu_affinity(), "one_core_affinity_requested": args.one_core,
              "load_seconds": load_seconds, "inference_cpu_seconds": time.process_time() - cpu_start,
              "elapsed_seconds": time.perf_counter() - start, **memory, "measurements": measurements,
              "official_calls": 0, "paid_api_calls": 0, "production_modified": False}
    (args.output / (args.worker + ".json")).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"variant": args.worker, "elapsed_seconds": round(report["elapsed_seconds"], 3), **memory}), flush=True)


def controller(args):
    create_fixture(args)
    (args.output / "frozen-tool.py").write_bytes(Path(__file__).read_bytes())
    variants = args.variants or list(VARIANTS)
    for name in variants:
        command = [sys.executable, str(Path(__file__).resolve()), "--model-dir", str(args.model_dir.resolve()),
                   "--output", str(args.output.resolve()), "--worker", name]
        if args.one_core:
            command.append("--one-core")
        subprocess.run(command, check=True, timeout=240,
                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    reports = {name: json.loads((args.output / (name + ".json")).read_text(encoding="utf-8")) for name in variants}
    reference = reports[variants[0]]
    comparisons = {}
    for name, report in reports.items():
        assert report["fixture_sha256"] == reference["fixture_sha256"]
        diff = max(abs(a - b) for old, new in zip(reference["measurements"], report["measurements"], strict=True)
                   for a, b in zip(old["scores"], new["scores"], strict=True))
        same_order = all(old["score_order"] == new["score_order"]
                         for old, new in zip(reference["measurements"], report["measurements"], strict=True))
        times = [row["seconds"] for row in report["measurements"]]
        reference_seconds = sum(row["seconds"] for row in reference["measurements"])
        comparisons[name] = {"inference_seconds_total": sum(times), "median_case_seconds": statistics.median(times),
                             "maximum_case_seconds": max(times), "speedup_vs_basic_t2": reference_seconds / sum(times),
                             "maximum_absolute_logit_difference": diff, "all_score_orders_equal": same_order,
                             "rss_peak_sampled_bytes": report["rss_peak_sampled_bytes"]}
    out = {"purpose": __doc__, "completed": True, "fixture_sha256": reference["fixture_sha256"],
           "cases": len(reference["measurements"]) // 2, "repeats": 2, "comparisons": comparisons,
           "one_core_affinity_requested": args.one_core, "reference_variant": variants[0],
           "limitations": ["Local Windows CPU, not Linux one-core quota or cloud memory proof.",
                           "Already used development queries; runtime/order comparison only, not independent relevance or coding success.",
                           "Nonzero logit differences or ordering differences require explicit investigation before adoption."],
           "production_modified": False, "official_calls": 0, "full_allowed": False}
    (args.output / "summary.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(out), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--data", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", choices=VARIANTS)
    parser.add_argument("--one-core", action="store_true", help="Restrict each worker to the first allowed logical CPU; not equivalent to Linux cgroup quota")
    parser.add_argument("--variants", nargs="+", choices=VARIANTS)
    arguments = parser.parse_args()
    if arguments.worker:
        worker(arguments)
    else:
        assert arguments.data is not None
        controller(arguments)
