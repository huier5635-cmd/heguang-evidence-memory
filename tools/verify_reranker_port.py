"""Check a bounded public sample against the exact research scores and API content."""
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
from heguang_code.reranker import PairScorer
from heguang_code.schema import SearchRequest
from heguang_code.store import Store, digest
from research.reranker import Reranker
from public_benchmark import load_public


def run(args):
    targets, _, _, relation, _ = load_public(args.data)
    reference = json.loads(args.reference.read_text(encoding="utf-8"))
    cases = reference["cases"]
    ids = {row["target_id"] for row in sorted(cases, key=lambda row: hashlib.sha256(row["target_id"].encode()).hexdigest())[:6]}
    ids.add(min(cases, key=lambda row: len(targets[row["target_id"]]["problem_statement"]))["target_id"])
    ids.add(max(cases, key=lambda row: len(targets[row["target_id"]]["problem_statement"]))["target_id"])
    research = Reranker(args.model_dir, args.cache)
    model = PairScorer(args.model_dir)
    settings = Settings(data_dir=args.index / "db", api_key="public-port-verification-only", profile="rarity")
    store, measurements = Store(settings), []
    try:
        app = create_app(settings, scorer=model)
        with TestClient(app, headers={"X-Api-Key": settings.api_key}) as client:
            for iid in sorted(ids):
                query = targets[iid]["problem_statement"]
                user = "public-coding:" + digest([iid, 16])[:24]
                lexical = store.search(SearchRequest(user_id=user, query=query, top_k=100))
                documents = [row["content"].split("\n", 1)[1] for row in lexical[:16]]
                expected = research.scores(query, documents)
                actual = model.scores(query, documents)
                error = max((abs(a - b) for a, b in zip(expected, actual)), default=0)
                assert len(expected) == len(actual) and error < 1e-5
                response = client.post("/search", json={"user_id": user, "query": query, "top_k": 100})
                assert response.status_code == 200
                rows = response.json()["data"]
                assert {row["id"]: row["content"] for row in rows} == {row["id"]: row["content"] for row in lexical}
                assert all(rows[i]["score"] >= rows[i + 1]["score"] for i in range(len(rows) - 1))
                first = next((rank for rank, row in enumerate(rows, 1)
                              if json.loads(row["content"].split("\n", 1)[0][8:-1])["session_id"] in relation[iid]), None)
                prior = next(row for row in cases if row["target_id"] == iid)["weights"]["1"]["first_positive_chunk_rank"]
                assert first == prior
                measurements.append({"target_id": iid, "candidate_rows": len(lexical),
                                     "maximum_pair_score_error": error, "first_positive_rank": first,
                                     "original_evidence_preserved": True})
    finally:
        research.close()
    report = {"purpose": __doc__, "passed": True, "cases": measurements,
              "selection": "Six public development cases by SHA256 plus shortest/longest visible query",
              "reference_report_sha256": hashlib.sha256(args.reference.read_bytes()).hexdigest(),
              "source_sha256": {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                                for path in sorted((ROOT / "heguang_code").glob("*.py"))},
              "official_submissions": 0, "paid_api_calls": 0,
              "limitations": "Port consistency on a bounded public development sample; not a new held-out quality estimate or cloud load test."}
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": True, "public_cases": len(measurements), "max_pair_score_error": max(row["maximum_pair_score_error"] for row in measurements)}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    run(parser.parse_args())
