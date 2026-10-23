"""Freeze a public-only local two-draft pilot from an already verified paired input plan."""
import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research.local_codegen import prepare
from acquire_local_codegen import MODEL_HASH, RUNTIME_HASH


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent-plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    parent = args.parent_plan.read_bytes()
    plan = prepare(json.loads(parent))
    plan.update({"frozen_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                 "parent_plan_sha256": hashlib.sha256(parent).hexdigest(),
                 "model_sha256": MODEL_HASH, "runtime_archive_sha256": RUNTIME_HASH})
    plan["server"]["repack"] = False
    names = ["research/local_codegen.py", "tools/run_local_paired_codegen.py", "tools/acquire_local_codegen.py",
             "tools/prepare_local_codegen_plan.py", "heguang_code/reranker.py"]
    plan["source_sha256"] = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in names}
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(plan, output, indent=2)
        output.write("\n")
    print(json.dumps({"frozen": True, "case": plan["instance_id"], "memory_records": plan["local_memory_prefix"]["returned_records"],
                      "generation_requests_started": 0, "automatic_code_execution": False}), flush=True)
