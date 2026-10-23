"""Verify selected API output against the pre-integration public selection study."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from heguang_code.api import create_app
from heguang_code.config import Settings
from heguang_code.reranker import PairScorer, fit_budget, reorder
from heguang_code.schema import SearchRequest
from heguang_code.store import Store, digest
from research.reranker import Reranker
from public_benchmark import load_public
from selection_ablation import metrics


def run(args):
    targets, histories, _, relation, _ = load_public(args.data)
    reference = json.loads(args.reference.read_text(encoding="utf-8"))
    assert reference["split"] == "dev" and reference["complete"]
    cases = reference["cases"]
    selected = {row["target_id"] for row in sorted(cases, key=lambda row: hashlib.sha256(row["target_id"].encode()).hexdigest())[:6]}
    selected.add(min(cases, key=lambda row: len(targets[row["target_id"]]["problem_statement"]))["target_id"])
    selected.add(max(cases, key=lambda row: len(targets[row["target_id"]]["problem_statement"]))["target_id"])
    research = Reranker(args.model_dir, args.cache)
    model = PairScorer(args.model_dir)
    settings = Settings(data_dir=args.index / "db", api_key="public-selection-port-only", profile="rarity")
    store, measured = Store(settings), []
    try:
        with TestClient(create_app(settings, scorer=model), headers={"X-Api-Key": settings.api_key}) as client:
            for iid in sorted(selected):
                query = targets[iid]["problem_statement"]
                user = "public-coding:" + digest([iid, reference["noise_requested_per_positive"]])[:24]
                pool = store.search(SearchRequest(user_id=user, query=query, top_k=100), selection_pool=True)
                documents = [row["content"].split("\n", 1)[1] for row in pool[:16]]
                expected_scores, actual_scores = research.scores(query, documents), model.scores(query, documents)
                assert len(expected_scores) == len(actual_scores)
                error = max((abs(a - b) for a, b in zip(expected_scores, actual_scores)), default=0)
                assert error < 1e-5
                expected = fit_budget(reorder(pool, expected_scores, weight=4), top_k=100, characters=128000)
                response = client.post("/search", json={"user_id": user, "query": query, "top_k": 100})
                assert response.status_code == 200
                actual = response.json()["data"]
                assert actual == expected
                aggregate = metrics(actual, relation[iid], histories)
                frozen = next(row for row in cases if row["target_id"] == iid)["variants"]["select_weight4_pack_128000"]
                assert aggregate == frozen
                measured.append({"target_id": iid, "maximum_pair_score_error": error,
                                 "frozen_study_metrics_matched": True, "returned_chars": aggregate["returned_characters"]})
    finally:
        research.close()
    report = {"passed": True, "purpose": __doc__, "cases": measured,
              "reference_report_sha256": hashlib.sha256(args.reference.read_bytes()).hexdigest(),
              "source_sha256": {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                                for path in sorted((ROOT / "heguang_code").glob("*.py"))},
              "official_score": False, "official_submissions": 0, "paid_api_calls": 0,
              "limitations": "Eight public development cases verify port behavior. This is not independent quality validation, coding resolution or cloud capacity."}
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": True, "cases": len(measured), "maximum_pair_score_error": max(row["maximum_pair_score_error"] for row in measured)}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args())
