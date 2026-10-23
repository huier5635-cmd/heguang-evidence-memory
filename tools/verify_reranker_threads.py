"""Verify the real single-thread constructor against frozen development scores."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from heguang_code.reranker import PairScorer, reorder


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args):
    fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
    reference = json.loads(args.reference.read_text(encoding="utf-8"))
    assert fixture["official_data"] is False and not fixture["new_blind_quality_test"]
    assert reference["fixture_sha256"] == sha(args.fixture)
    expected = {row["case_id"]: row for row in reference["measurements"] if row["repeat"] == 0}
    scorer = PairScorer(args.model_dir, threads=1)
    options = scorer.session.get_session_options()
    assert options.intra_op_num_threads == 1
    assert options.get_session_config_entry("session.intra_op.allow_spinning") == "0"
    assert options.get_session_config_entry("session.inter_op.allow_spinning") == "0"
    cases, started = [], time.perf_counter()
    for case in fixture["cases"]:
        originals = list(case["documents"])
        actual = scorer.scores(case["query"], case["documents"])
        old = expected[case["id"]]["scores"]
        assert actual == old, "Changed model scores"
        rows = [{"id": str(index), "content": text, "score": 1 / (index + 1)} for index, text in enumerate(originals)]
        assert reorder(rows, actual, weight=4) == reorder(rows, old, weight=4)
        assert case["documents"] == originals
        cases.append({"id": case["id"], "documents": len(actual), "exact_scores_equal": True,
                      "exact_final_rankings_equal": True, "maximum_logit_difference": 0})
    report = {"purpose": __doc__, "passed": True, "version": "0.2.0.dev5",
              "fixture_sha256": sha(args.fixture), "reference_sha256": sha(args.reference),
              "source_sha256": {p.relative_to(ROOT).as_posix(): sha(p) for p in sorted((ROOT / "heguang_code").glob("*.py"))},
              "tool_sha256": sha(Path(__file__)), "cases": cases, "elapsed_seconds": time.perf_counter() - started,
              "actual_model_threads": options.intra_op_num_threads,
              "limitations": "Eight already used public development queries and two synthetic queries; constructor and numerical/ranking parity only. Not a new independent quality test or cloud API timing.",
              "official_calls": 0, "paid_api_calls": 0, "full_allowed": False}
    with args.output.open("x", encoding="utf-8") as target:
        json.dump(report, target, indent=2)
        target.write("\n")
    print(json.dumps({"passed": True, "cases": len(cases), "documents": sum(row["documents"] for row in cases),
                      "exact_scores_equal": True, "threads": options.intra_op_num_threads}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("model-dir", "fixture", "reference", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    run(parser.parse_args())
