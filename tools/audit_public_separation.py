"""Count-only public-input audit; never evaluates sealed queries or reads target answers."""
import argparse
import hashlib
import json
from pathlib import Path

from public_benchmark import load_public, ROOT


def run(data, output):
    targets, history, _, relation, audit = load_public(data)
    overlap = sorted(set(targets) & set(history))
    eligible_overlap = sorted(set(relation) & set(history))
    assert not eligible_overlap, "An evaluated target appears in historical memories"
    development_reports = list((ROOT / "results").glob("dev7-selection-weights-*.json"))
    inspected = []
    for path in development_reports:
        report = json.loads(path.read_text(encoding="utf-8"))
        assert report["split"] == "dev"
        assert all(row["target_id"] in relation and row["target_id"] not in history for row in report["cases"])
        inspected.append({"path": path.relative_to(ROOT).as_posix(),
                          "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                          "evaluated_queries": len(report["cases"])})
    case_ids = ["sympy__sympy-27868", "psf__requests-3481"]
    assert all(iid in relation and iid not in history for iid in case_ids)
    report = {"passed": True, "purpose": __doc__, "data_audit": audit,
              "unique_unambiguous_target_ids": len(targets),
              "all_unambiguous_target_history_overlap": overlap,
              "eligible_target_history_overlap": eligible_overlap,
              "manual_public_cases_absent_from_history": case_ids,
              "selection_reports_checked": inspected,
              "target_gold_solution_fields_loaded": [], "sealed_query_evaluations": 0,
              "qualification": "One excluded/ineligible target also exists in the history table; no eligible evaluated target does. History patches are allowed source memories. Audit does not execute target tests. The separate public-case evaluator read test oracles only after first code freeze, never target gold solutions."}
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": True, "eligible_queries": len(relation),
                      "eligible_history_overlap": len(eligible_overlap), "sealed_queries_evaluated": 0}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.data, args.output)
