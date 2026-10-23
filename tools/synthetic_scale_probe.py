"""Bounded, unique synthetic command/output scaling probe. Never an official score."""
import argparse
import hashlib
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from heguang_code.config import Settings
from heguang_code.normalize import trajectory_views
from heguang_code.schema import AddRequest, Message, SearchRequest
from heguang_code.store import Store, digest


def fixture(index):
    marker = f"case_marker_{index:08}"
    command = f"pytest tests/test_parser_{index:08}.py -q"
    output = (f"FAILED tests/test_parser_{index:08}.py::test_missing_packet\n"
              f"File src/parser_{index:08}.py, line 42, in parse_packet\n"
              f"AssertionError: missing packet must preserve state; {marker}\n"
              + "\n".join(f"context frame {line}: state[{line}] = decode_packet({index}, {line})"
                            for line in range(16))
              + f"\nDiff src/parser_{index:08}.py\n+return packet if packet is not None else []\n")
    return [
        {"role": "assistant", "content": "", "tool_calls": [{"id": f"call-{index}", "function": {
            "name": "bash", "arguments": json.dumps({"command": command})}}]},
        {"role": "tool", "tool_call_id": f"call-{index}", "content": output,
         "extra": {"raw_output": output, "returncode": 1}},
    ]


def worker(args):
    store = Store(Settings(data_dir=args.work / "db", api_key="synthetic-local-only", profile="rarity"))
    user = "one-large-synthetic-repository"
    started, source_chars, input_bytes = time.perf_counter(), 0, 0
    for start in range(0, args.steps, 50):
        messages = [message for index in range(start, min(args.steps, start + 50)) for message in fixture(index)]
        req = AddRequest(user_id=user, request_id=f"batch-{start}", session_id=f"run-{start // 50}", messages=messages)
        source_chars += sum(len(view[0]) for view in trajectory_views(req.messages))
        input_bytes += len(req.model_dump_json().encode())
        store.add(req)
        if start % 1000 == 0:
            print(json.dumps({"event": "synthetic_ingest", "steps": min(args.steps, start + 50),
                              "elapsed_s": round(time.perf_counter() - started, 2)}), flush=True)
    add_seconds = time.perf_counter() - started
    # Reopen to verify persisted state rather than an in-process cache.
    store = Store(store.settings)
    rng = random.Random(20261023)
    probes = sorted(set([0, args.steps - 1, *rng.sample(range(args.steps), min(48, args.steps))]))
    latencies, restored, hit1 = [], 0, 0
    db = store.connect(user, create=False)
    try:
        counts = {table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                  for table in ("sources", "docs", "blobs", "spans", "writes")}
        assert counts["sources"] == args.steps * 2
        for index in probes:
            start = index // 50 * 50
            messages = [Message(**message) for message in fixture(index)]
            views = list(trajectory_views(messages))
            for position in (0, 1):
                source_id = digest([user, f"batch-{start}", (index - start) * 2 + position])[:40]
                expected = views[position][0]
                assert store.source_span(db, source_id, 0, len(expected), {}) == expected
                restored += 1
            tick = time.perf_counter()
            result = store.search(SearchRequest(user_id=user, query=f"case_marker_{index:08}", top_k=5))
            latencies.append((time.perf_counter() - tick) * 1000)
            hit1 += bool(result and f"case_marker_{index:08}" in result[0]["content"])
        assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        db.close()
    assert hit1 == len(probes)
    result = {"completed": True, "synthetic_steps": args.steps, "messages": args.steps * 2,
              "input_json_bytes": input_bytes, "normalized_source_characters": source_chars,
              "counts": counts, "add_seconds": round(add_seconds, 3),
              "sampled_source_messages_restored_exactly": restored,
              "identifier_lookup_cases": len(probes), "identifier_hit_at_1": hit1 / len(probes),
              "search_p95_ms": round(sorted(latencies)[int((len(latencies) - 1) * .95)], 3),
              "database_bytes": sum(path.stat().st_size for path in store.settings.data_dir.glob("*.sqlite3"))}
    (args.work / "worker-result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


def run(args):
    args.work.mkdir(parents=True, exist_ok=False)
    tracked, peak_rss, peak_disk, stopped = {}, 0, 0, None
    started = time.perf_counter()
    with (args.work / "worker.log").open("x", encoding="utf-8") as log:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--worker", "--work",
                                    str(args.work.resolve()), "--steps", str(args.steps)],
                                   cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                   env={**os.environ, "PYTHONUTF8": "1"})
        try:
            while process.poll() is None:
                parent = psutil.Process(process.pid)
                rss = 0
                for child in [parent, *parent.children(recursive=True)]:
                    try:
                        tracked[child.pid] = child.create_time()
                        rss += child.memory_info().rss
                    except psutil.NoSuchProcess:
                        pass
                peak_rss = max(peak_rss, rss)
                size = 0
                for path in (args.work / "db").glob("*.sqlite3*"):
                    try:
                        size += path.stat().st_size
                    except FileNotFoundError:
                        pass
                peak_disk = max(peak_disk, size)
                if rss > args.rss_mib * 1024 ** 2 or size > args.disk_mib * 1024 ** 2 or time.perf_counter() - started > args.seconds:
                    stopped = "resource_guard"
                    break
                time.sleep(.05)
        except psutil.NoSuchProcess:
            stopped = "worker_exited_during_sample"
        except Exception:
            stopped = "observer_failure"
            raise
        finally:
            live = []
            for pid, created_at in tracked.items():
                try:
                    child = psutil.Process(pid)
                    if child.create_time() == created_at:
                        child.terminate()
                        live.append(child)
                except psutil.NoSuchProcess:
                    pass
            _, alive = psutil.wait_procs(live, timeout=5)
            for child in alive:
                child.kill()
            if alive:
                psutil.wait_procs(alive, timeout=5)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    remaining = []
    for pid, created_at in tracked.items():
        try:
            child = psutil.Process(pid)
            if child.create_time() == created_at and child.is_running():
                remaining.append(pid)
        except psutil.NoSuchProcess:
            pass
    path = args.work / "worker-result.json"
    result = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"completed": False}
    report = {"purpose": "Bounded single-namespace synthetic scaling proof; identifier lookup is structural only",
              **result, "stop_reason": stopped or ("completed" if result["completed"] else "worker_failure"),
              "peak_worker_tree_rss_bytes": peak_rss, "peak_database_and_wal_bytes": peak_disk,
              "worker_reaped": process.poll() is not None and not remaining,
              "remaining_worker_processes": remaining,
              "resource_measurement": "Sum of sampled RSS of launcher and recursive worker children; shared pages may double count",
              "guards": {"rss_mib": args.rss_mib, "disk_mib": args.disk_mib, "seconds": args.seconds},
              "elapsed_seconds": round(time.perf_counter() - started, 3),
              "source_sha256": {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                                for path in sorted((ROOT / "heguang_code").glob("*.py"))},
              "official_submissions": 0, "real_model_calls": 0,
              "limitations": "Unique generated command/output pairs in one local namespace. These are not public-task relevance labels, resolved coding tasks, HTTP concurrency, 1.8-billion-token capacity, or cloud proof."}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report), flush=True)
    assert report["completed"] and report["worker_reaped"] and report["stop_reason"] == "completed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("results/synthetic-scale.json"))
    parser.add_argument("--steps", type=int, default=12500)
    parser.add_argument("--seconds", type=int, default=180)
    parser.add_argument("--rss-mib", type=int, default=384)
    parser.add_argument("--disk-mib", type=int, default=512)
    parser.add_argument("--worker", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.steps <= 100000:
        raise ValueError("Choose 1..100000 synthetic steps")
    worker(args) if args.worker else run(args)
