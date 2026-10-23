"""Two named free-model draft requests only; does not apply or execute model edits."""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research.free_codegen import run_plan

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--plan", type=Path, required=True)
parser.add_argument("--model", required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--execute-public-requests", action="store_true")
args = parser.parse_args()
plan = json.loads(args.plan.read_text(encoding="utf-8"))
if args.execute_public_requests:
    run_plan(plan, args.model, os.getenv("PUBLIC_EVAL_API_KEY", ""), args.output)
    print(json.dumps({"completed_draft_requests": 2, "code_executed": False, "official_submissions": 0}))
else:
    print(json.dumps({"prepared_only": True, "model": args.model, "arms": plan["arm_order"],
                      "requires_key": not bool(os.getenv("PUBLIC_EVAL_API_KEY")),
                      "no_model_requests_started": True, "code_executed": False}))
