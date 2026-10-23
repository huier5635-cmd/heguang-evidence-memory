"""Evaluate two reviewed, already frozen local drafts on one PUBLIC development case."""
import argparse
import difflib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research.local_codegen import proposed_files


def sha(body):
    return hashlib.sha256(body).hexdigest()


def run(args):
    generation = json.loads((args.drafts / "result.json").read_text(encoding="utf-8"))
    assert generation["complete"] and generation["worker_reaped"]
    plan_path = args.drafts / "frozen-plan.json"
    assert sha(plan_path.read_bytes()) == generation["frozen_plan_sha256"]
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan["instance_id"] == "sympy__sympy-27868" and plan["official_data"] is False
    parent_bytes = args.parent_plan.read_bytes()
    assert sha(parent_bytes) == plan["parent_plan_sha256"]
    parent = json.loads(parent_bytes)
    assert set(args.reviewed_response_sha256) == {row["response_sha256"] for row in generation["drafts"].values()}
    visible = json.loads(plan["messages"]["without_memory"][1]["content"])
    originals = {row["path"]: row["source"] for row in visible["unmodified_base_source_files"]}
    assert set(originals) == {"sympy/parsing/mathematica.py"}
    for name, expected in plan["allowed_file_sha256"].items():
        assert sha(originals[name].encode()) == expected
    args.work.mkdir(parents=True, exist_ok=False)
    proposals, invalid = {}, {}
    # Freeze complete proposed source before any target oracle column is read.
    for arm in plan["arm_order"]:
        raw = (args.drafts / (arm + ".json")).read_bytes()
        assert sha(raw) == generation["drafts"][arm]["response_sha256"]
        try:
            content = json.loads(raw)["choices"][0]["message"]["content"]
            changed = proposed_files(content, originals)
            proposals[arm] = changed
            for name, source in changed.items():
                target = args.work / "frozen-proposals" / arm / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.encode())
            patch = "".join(difflib.unified_diff(originals["sympy/parsing/mathematica.py"].splitlines(True),
                changed["sympy/parsing/mathematica.py"].splitlines(True), fromfile="a/sympy/parsing/mathematica.py",
                tofile="b/sympy/parsing/mathematica.py"))
            (args.work / (arm + ".patch")).write_bytes(patch.encode())
        except (ValueError, TypeError, KeyError):
            invalid[arm] = "invalid_exact_edits"
    frozen = {"generation_result_sha256": sha((args.drafts / "result.json").read_bytes()),
              "reviewed_response_sha256": args.reviewed_response_sha256,
              "source_sha256": {arm: {name: sha(source.encode()) for name, source in value.items()}
                                for arm, value in proposals.items()}, "invalid_drafts": invalid,
              "target_gold_solution_read": False, "target_oracle_loaded_at_freeze": False}
    freeze_path = args.work / "candidate-code-freeze.json"
    freeze_path.write_text(json.dumps(frozen, indent=2) + "\n", encoding="utf-8")

    fields = ["instance_id", "test_patch", "FAIL_TO_PASS", "PASS_TO_PASS"]
    oracle_file = args.data / "SWEContextBench/data/SWEContextBench_Related.parquet"
    assert sha(oracle_file.read_bytes()) == parent["data_audit"]["source_sha256"][oracle_file.name]
    rows = pq.read_table(oracle_file, columns=fields, filters=[("instance_id", "=", plan["instance_id"])]).to_pylist()
    assert len(rows) == 1
    oracle = rows[0]
    parse = lambda value: json.loads(value) if isinstance(value, str) else value
    ftp, ptp = parse(oracle["FAIL_TO_PASS"]) or [], parse(oracle["PASS_TO_PASS"]) or []
    test_ids = sorted(set(ftp + ptp))
    assert test_ids and all(node.split("::")[0] == "sympy/parsing/tests/test_mathematica.py" for node in test_ids)
    patch = oracle["test_patch"]
    changed_tests = [line[6:] for line in patch.splitlines() if line.startswith("+++ b/")]
    assert changed_tests and all(name.startswith("sympy/parsing/tests/") or name == "sympy/testing/quality_unicode.py" for name in changed_tests)
    assert "+++ /dev/null" not in patch and "mode 120000" not in patch and len(patch.encode()) <= 1024 ** 2
    for name in changed_tests:
        assert not Path(name).is_absolute() and ".." not in Path(name).parts and name.endswith(".py")
    oracle_path = args.work / "public-test-only.patch"
    oracle_path.write_bytes(patch.encode())
    if args.base_archive:
        assert args.base_archive.stat().st_size <= 50 * 1024 ** 2
        archive_bytes = args.base_archive.read_bytes()
    else:
        with urllib.request.urlopen(parent["base_archive_url"], timeout=60) as response:
            archive_bytes = response.read(50 * 1024 ** 2 + 1)
    assert len(archive_bytes) <= 50 * 1024 ** 2 and sha(archive_bytes) == parent["base_archive_sha256"]
    base = args.work / "base-source"
    base.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
        members = archive.getmembers()
        assert len(members) <= 10000 and sum(member.size for member in members) <= 250 * 1024 ** 2
        for member in members:
            relative = Path(*Path(member.name).parts[1:])
            destination = (base / relative).resolve()
            assert destination.is_relative_to(base.resolve()) and (member.isdir() or member.isfile())
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source:
                    destination.write_bytes(source.read())
    for name, expected in plan["allowed_file_sha256"].items():
        assert sha((base / name).read_bytes()) == expected
    results = {}
    for phase in ["baseline", *plan["arm_order"]]:
        if phase in invalid:
            results[phase] = {"status": invalid[phase], "resolved": False, "tests_executed": False}
            continue
        destination = args.work / phase
        shutil.copytree(base, destination)
        if phase != "baseline":
            for name, source in proposals[phase].items():
                (destination / name).write_bytes(source.encode())
        hashes = {name: sha((destination / name).read_bytes()) for name in originals}
        subprocess.run(["git", "apply", "--check", str(oracle_path.resolve())], cwd=destination, check=True, capture_output=True)
        subprocess.run(["git", "apply", str(oracle_path.resolve())], cwd=destination, check=True, capture_output=True)
        assert all(sha((destination / name).read_bytes()) == expected for name, expected in hashes.items())
        env = {name: value for name, value in os.environ.items() if name.upper() in {
            "SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "COMSPEC", "PATHEXT"}}
        env.update({"PYTHONPATH": str(destination.resolve()), "PYTHONIOENCODING": "utf-8", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"})
        xml = (args.work / (phase + ".xml")).resolve()
        try:
            process = subprocess.run([sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider",
                "--junitxml=" + str(xml), *test_ids], cwd=destination, env=env, capture_output=True, timeout=120,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except subprocess.TimeoutExpired as error:
            (args.work / (phase + ".log")).write_bytes((error.stdout or b"") + (error.stderr or b""))
            results[phase] = {"status": "timeout", "source_sha256": hashes,
                              "test_outcomes": {}, "resolved": False, "tests_executed": True}
            continue
        (args.work / (phase + ".log")).write_bytes(process.stdout + process.stderr)
        outcomes = {}
        for case in ET.parse(xml).iter("testcase"):
            node = case.attrib["classname"].replace(".", "/") + ".py::" + case.attrib["name"]
            outcomes[node] = "failed" if case.find("failure") is not None else "error" if case.find("error") is not None else "skipped" if case.find("skipped") is not None else "passed"
        resolved = phase != "baseline" and bool(ftp) and all(results["baseline"].get("test_outcomes", {}).get(node) == "failed"
            and outcomes.get(node) == "passed" for node in ftp) and all(outcomes.get(node) == "passed" for node in ptp)
        results[phase] = {"status": "tested", "exit_code": process.returncode, "source_sha256": hashes,
                          "test_outcomes": outcomes, "resolved": bool(resolved), "tests_executed": True}
    baseline_outcomes = results["baseline"].get("test_outcomes", {})
    admissible = bool(ftp) and all(baseline_outcomes.get(node) == "failed" for node in ftp) and all(baseline_outcomes.get(node) == "passed" for node in ptp)
    report = {"instance_id": plan["instance_id"], "purpose": __doc__, "phases": results, "baseline_admissible": admissible,
        "candidate_code_freeze_sha256": sha(freeze_path.read_bytes()), "oracle_fields": fields,
        "public_test_patch_sha256": sha(patch.encode()), "target_gold_solution_read": False,
        "model_received_target_oracle": False, "manual_public_case_already_developed": True,
        "memory_resolved_delta_on_this_one_case": (int(results["with_memory"]["resolved"]) - int(results["without_memory"]["resolved"])) if admissible else None,
        "blind_generalization_score": False, "official_score": False, "paid_api_calls": 0,
        "limitations": "One already studied public development case with a local quantized model, one-shot edits and reduced context. Not the official model/agent, not multiple independent tasks, no statistical memory-benefit claim."}
    (args.work / "result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"case": report["instance_id"], "resolved": {phase: result["resolved"] for phase, result in results.items()},
                      "memory_delta": report["memory_resolved_delta_on_this_one_case"], "official_score": False}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("data", "drafts", "parent-plan", "work"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--reviewed-response-sha256", nargs=2, required=True)
    parser.add_argument("--base-archive", type=Path, help="Optional cached public archive; same pinned SHA256 is mandatory")
    run(parser.parse_args())
