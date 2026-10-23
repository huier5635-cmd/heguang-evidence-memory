"""One frozen public repository holdout comparison; no coding agent or official run."""
import argparse
import hashlib
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from heguang_code.config import Settings
from heguang_code.reranker import fit_budget, reorder
from heguang_code.schema import AddRequest, SearchRequest
from heguang_code.store import Store, digest
from research.reranker import Reranker, MODEL_SHA256
from public_benchmark import load_public, render_history, split_for, issue_number
from selection_ablation import metrics


def verify(plan, data, model_dir):
    assert plan["official_submission_allowed"] is False and plan["split"] == "sealed_test"
    assert plan["model_sha256"] == MODEL_SHA256
    for name, expected in plan["source_sha256"].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected, name
    for name, expected in plan["input_sha256"].items():
        assert hashlib.sha256((data / name).read_bytes()).hexdigest() == expected, name
    assert hashlib.sha256((model_dir / "tokenizer.json").read_bytes()).hexdigest() == plan["tokenizer_sha256"]
    assert plan["variants"] == {"legacy": {"weight": 1, "characters": 32000, "selection_pool": False},
                                 "candidate": {"weight": 4, "characters": 128000, "selection_pool": True}}


def run(args):
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    verify(plan, args.data, args.model_dir)
    plan_hash = hashlib.sha256(args.plan.read_bytes()).hexdigest()
    result_path = args.work / "result.json"
    if args.resume:
        previous = json.loads(result_path.read_text(encoding="utf-8"))
        assert not previous["complete"] and previous["frozen_plan_sha256"] == plan_hash
        cases = previous["cases"]
    else:
        args.work.mkdir(parents=True, exist_ok=False)
        cases = []
    targets, histories, by_repo, relation, audit = load_public(args.data)
    selected = sorted((iid for iid in relation if split_for(targets[iid]["repo"]) == "sealed_test"),
                      key=lambda iid: hashlib.sha256(("order:" + iid).encode()).hexdigest())
    assert len(selected) == plan["expected_queries"] == 7
    assert not set(selected) & set(histories)
    held_repos = {targets[iid]["repo"] for iid in selected}
    other_repos = {targets[iid]["repo"] for iid in relation if split_for(targets[iid]["repo"]) != "sealed_test"}
    assert not held_repos & other_repos
    store = Store(Settings(data_dir=args.work / "db", api_key="public-holdout-only", profile="rarity"))
    scorer = Reranker(args.model_dir, args.work / "pair-cache.sqlite3", seconds=plan["limits"]["seconds"],
                      rss_mib=plan["limits"]["rss_mib"])
    started, complete = time.perf_counter(), False
    finished = {(row["target_id"], row["noise_requested_per_positive"]) for row in cases}
    try:
        for iid in selected:
            target, gold = targets[iid], relation[iid]
            negatives = sorted((row for row in by_repo[target["repo"]]
                if row["instance_id"] not in gold and row["instance_id"] != iid
                and issue_number(row["instance_id"]) < issue_number(iid)
                and row["created_at"] and row["created_at"] < target["created_at"]), key=lambda row: row["instance_id"])
            random.Random(int(hashlib.sha256(("noise:" + iid).encode()).hexdigest(), 16)).shuffle(negatives)
            for noise in plan["noise"]:
                if (iid, noise) in finished:
                    continue
                chosen = [histories[key] for key in sorted(gold)] + negatives[:min(len(negatives), noise * len(gold))]
                random.Random(int(hashlib.sha256(("case:" + iid + ":" + str(noise)).encode()).hexdigest(), 16)).shuffle(chosen)
                user = "public-holdout:" + digest([iid, noise])[:24]
                for row in chosen:
                    store.add(AddRequest(user_id=user, request_id=row["instance_id"], session_id=row["instance_id"],
                                         messages=[{"role": "user", "content": render_history(row)[0]}]))
                request = SearchRequest(user_id=user, query=target["problem_statement"], top_k=100)
                measures, durations = {}, {}
                # Both algorithms were fixed in the plan before any holdout result was opened.
                for label, configuration in plan["variants"].items():
                    tick = time.perf_counter()
                    pool = store.search(request, selection_pool=configuration["selection_pool"])
                    scores = scorer.scores(request.query, [row["content"].split("\n", 1)[1] for row in pool[:16]])
                    ranked = reorder(pool, scores, weight=configuration["weight"])
                    rows = fit_budget(ranked, top_k=100, characters=configuration["characters"])
                    measures[label] = metrics(rows, gold, histories)
                    durations[label] = round((time.perf_counter() - tick) * 1000, 3)
                cases.append({"target_id": iid, "repository": target["repo"], "noise_requested_per_positive": noise,
                              "history_count": len(chosen), "positive_count": len(gold), "variants": measures,
                              "local_ms_with_public_cache": durations})
                print(json.dumps({"event": "frozen_holdout_case", "completed": len(cases),
                                  "total": len(selected) * len(plan["noise"]),
                                  "elapsed_s": round(time.perf_counter() - started, 2)}), flush=True)
        complete = True
    finally:
        scorer.close()
        aggregates = {}
        for noise in plan["noise"]:
            sample = [row for row in cases if row["noise_requested_per_positive"] == noise]
            if sample:
                aggregates[str(noise)] = {label: {key: round(sum(row["variants"][label][key] for row in sample) / len(sample), 6)
                    for key in sample[0]["variants"][label] if key != "first_positive_rank"} for label in plan["variants"]}
        report = {"purpose": __doc__, "complete": complete, "split": "sealed_test", "unique_queries": len(selected),
                  "cases_completed": len(cases), "frozen_plan_sha256": plan_hash, "model_sha256": MODEL_SHA256,
                  "source_sha256": plan["source_sha256"], "data_audit": audit,
                  "aggregate": aggregates, "cases": cases, "elapsed_seconds_this_invocation": round(time.perf_counter() - started, 3),
                  "observed_rss_bytes": scorer.observed_rss, "official_submissions": 0, "paid_api_calls": 0,
                  "limitations": "Seven public queries from previously untested repositories, with repeated noise conditions. This is one frozen retrieval proxy test, not 28 independent tasks, code resolution, CAMBench, or proof of prize eligibility. Output budgets differ. Both model arms share a public research score cache, so their timings are not comparable. After this run these queries are spent holdout data; do not tune and call a rerun blind."}
        result_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"complete": complete, "unique_queries": len(selected), "aggregate": aggregates}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("data", "model-dir", "plan", "work"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    run(parser.parse_args())
