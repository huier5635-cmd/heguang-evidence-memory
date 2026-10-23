"""Bounded local HTTP test using a pinned public historical-trajectory sample.

No public competition service, private benchmark inputs, model calls or submissions.
"""
import argparse
import asyncio
import hashlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
import zlib
from collections import Counter
from pathlib import Path

import httpx
import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from heguang_code.config import Settings
from heguang_code.features import technical
from heguang_code.normalize import trajectory_views
from heguang_code.schema import Message
from heguang_code.store import Store, digest


def request_identity(iid, position, batch_size):
    return f"{iid}:part:{position // batch_size}", position % batch_size


def audit_messages(store, user, iid, messages, batch_size):
    db = store.connect(user, create=False)
    chars, cross_add = 0, 0
    roles = Counter()
    try:
        views = trajectory_views([Message(**message) for message in messages])
        for position, (message, view) in enumerate(zip(messages, views, strict=True)):
            expected, representation, paired_position = view
            request_id, local_position = request_identity(iid, position, batch_size)
            source_id = digest([user, request_id, local_position])[:40]
            assert store.source_span(db, source_id, 0, len(expected), {}) == expected
            parent = db.execute("SELECT paired_source_id FROM sources WHERE id=?", (source_id,)).fetchone()[0]
            if paired_position is not None:
                parent_request, parent_position = request_identity(iid, paired_position, batch_size)
                assert parent == digest([user, parent_request, parent_position])[:40]
                cross_add += parent_request != request_id
            else:
                assert parent is None
            original = db.execute("SELECT body,digest FROM raw_messages WHERE source_id=?", (source_id,)).fetchone()
            if set(message) - {"role", "content", "timestamp"}:
                decoded = zlib.decompress(original["body"])
                assert hashlib.sha256(decoded).hexdigest() == original["digest"]
                assert json.loads(decoded) == message
            chars += len(expected)
            roles[message["role"]] += 1
        assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    finally:
        db.close()
    return chars, roles, cross_add


async def run(args):
    args.work.mkdir(parents=True, exist_ok=False)
    receipt_path = args.data / args.receipt_name
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    settings = Settings(data_dir=args.work / "db", api_key="public-trajectory-http-only", profile="rarity")
    store = Store(settings)
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    environment = os.environ.copy()
    environment.update({"PYTHONPATH": str(ROOT), "HCM_DATA_DIR": str(settings.data_dir.resolve()),
                        "HCM_API_KEY": settings.api_key, "HCM_PROFILE": settings.profile, "HCM_MAX_CONCURRENCY": "2"})
    if args.model_dir:
        environment["HCM_RERANKER_DIR"] = str(args.model_dir.resolve())
    else:
        environment.pop("HCM_RERANKER_DIR", None)
    log = (args.work / "worker.log").open("x", encoding="utf-8")
    worker = subprocess.Popen([sys.executable, "-m", "uvicorn", "heguang_code.api:create_app", "--factory",
                               "--host", "127.0.0.1", "--port", str(port), "--workers", "1",
                               "--no-access-log", "--log-level", "warning"],
                              cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT)
    started = time.perf_counter()
    health_ms, health_errors, peak_rss, stop = [], 0, 0, None
    health_failures = []
    finished = asyncio.Event()
    operations, search_operations = [], []
    duplicate_retries, conflicting_retries = 0, 0
    message_count, input_bytes, source_chars, cross_add_pairs, completed_trajectories = 0, 0, 0, 0, 0
    role_counts = Counter()
    tracked = {}

    def current_process_tree():
        parent = psutil.Process(worker.pid)
        processes = [parent, *parent.children(recursive=True)]
        for process in processes:
            try:
                tracked[process.pid] = process.create_time()
            except psutil.NoSuchProcess:
                pass
        return processes

    async def sample_resources():
        nonlocal peak_rss, stop
        while not finished.is_set():
            try:
                rss = sum(process.memory_info().rss for process in current_process_tree() if process.is_running())
            except psutil.NoSuchProcess:
                if not finished.is_set():
                    stop = "worker_exited"
                return
            peak_rss = max(peak_rss, rss)
            size = 0
            for path in settings.data_dir.glob("*.sqlite3*"):
                try:
                    size += path.stat().st_size
                except FileNotFoundError:
                    pass
            if rss > args.rss_mib * 1024 ** 2 or size > args.disk_mib * 1024 ** 2 or time.perf_counter() - started > args.seconds:
                stop = "resource_guard"
                worker.terminate()
                return
            await asyncio.sleep(0.025)

    async def observer():
        nonlocal stop
        try:
            await sample_resources()
        except Exception:
            stop = "resource_observer_failure"
            if worker.poll() is None:
                worker.terminate()

    async def heartbeat(client):
        nonlocal health_errors
        while not finished.is_set():
            tick = time.perf_counter()
            try:
                response = await client.get("/health", timeout=2)
                if response.status_code == 200:
                    health_ms.append((time.perf_counter() - tick) * 1000)
                else:
                    health_errors += 1
                    health_failures.append({"elapsed_s": round(time.perf_counter() - started, 3), "status": response.status_code})
            except httpx.HTTPError as error:
                health_errors += 1
                health_failures.append({"elapsed_s": round(time.perf_counter() - started, 3), "error": type(error).__name__})
            await asyncio.sleep(0.1)

    watch, pulse = asyncio.create_task(observer()), None
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=60,
                                     headers={"Authorization": "Bearer " + settings.api_key}, trust_env=False) as client:
            async with asyncio.timeout(20):
                while True:
                    if worker.poll() is not None:
                        raise RuntimeError("Worker startup failed")
                    try:
                        response = await client.get("/health", timeout=1)
                        if response.status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(0.1)
            pulse = asyncio.create_task(heartbeat(client))
            parallel = asyncio.Semaphore(args.parallel_trajectories)
            async def process_one(index, item):
                nonlocal message_count, input_bytes, source_chars, cross_add_pairs, completed_trajectories
                nonlocal duplicate_retries, conflicting_retries
                async with parallel:
                    path = args.data / "VibeMemBench" / item["local_path"]
                    assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    messages = raw["messages"]
                    iid = item["path"].split("/")[3]
                    user = "public-trajectory:" + digest(item["path"].split("/")[2])[:24]
                    for start in range(0, len(messages), args.message_batch):
                        request_id, _ = request_identity(iid, start, args.message_batch)
                        payload = {"user_id": user, "session_id": iid, "request_id": request_id,
                                   "messages": messages[start:start + args.message_batch]}
                        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
                        tick = time.perf_counter()
                        if args.retry_first_batch and start == 0:
                            repeated = await asyncio.gather(*(client.post("/add", content=body, headers={"Content-Type": "application/json"}) for _ in range(2)))
                            assert all(row.status_code == 200 for row in repeated)
                            assert repeated[0].json() == repeated[1].json()
                            response = repeated[0]
                            duplicate_retries += 1
                            conflict = {**payload, "messages": [{"role": "user", "content": "conflicting retry must not replace original history"}]}
                            denied = await client.post("/add", json=conflict)
                            assert denied.status_code == 409
                            conflicting_retries += 1
                        else:
                            response = await client.post("/add", content=body, headers={"Content-Type": "application/json"})
                        assert response.status_code == 200, {"status": response.status_code, "file": item["local_path"]}
                        assert response.json() == {"success": True, "request_id": request_id, "user_id": user, "session_id": iid}
                        operations.append((time.perf_counter() - tick) * 1000)
                        input_bytes += len(body)
                    # Validation runs off the client's event loop so it cannot starve health sampling.
                    chars, roles, cross_add = await asyncio.to_thread(audit_messages, store, user, iid, messages, args.message_batch)
                    message_count += len(messages)
                    source_chars += chars
                    cross_add_pairs += cross_add
                    completed_trajectories += 1
                    role_counts.update(roles)
                    # This is a structural retrieval check, not a relevance/quality score.
                    path_candidates, symbols = await asyncio.to_thread(technical, "\n".join((message.get("content") or "") for message in messages))
                    query = next(iter(sorted(path_candidates or symbols)), "historical implementation")
                    searched = time.perf_counter()
                    response = await client.post("/search", json={"user_id": user, "query": query, "top_k": 100})
                    search_operations.append((time.perf_counter() - searched) * 1000)
                    assert response.status_code == 200
                    rows = response.json()["data"]
                    evidence_budget = settings.rerank_response_chars if args.model_dir else settings.response_chars
                    assert len(rows) <= 100 and sum(len(row["content"]) for row in rows) <= evidence_budget
                    assert all(rows[i]["score"] >= rows[i + 1]["score"] for i in range(len(rows) - 1))
                    for row in rows:
                        prefix, evidence = row["content"].split("\n", 1)
                        provenance = json.loads(prefix.removeprefix("[source ").removesuffix("]"))
                        db = store.connect(user, create=False)
                        try:
                            start, end = provenance["characters"]
                            assert store.source_span(db, provenance["source_id"], start, end, {}) == evidence
                        finally:
                            db.close()
                    print(json.dumps({"event": "trajectory_complete", "files": index + 1, "messages": message_count,
                                      "elapsed_s": round(time.perf_counter() - started, 2)}), flush=True)
            jobs = [asyncio.create_task(process_one(index, item)) for index, item in enumerate(receipt["files"])]
            try:
                await asyncio.gather(*jobs)
            finally:
                for job in jobs:
                    if not job.done():
                        job.cancel()
                await asyncio.gather(*jobs, return_exceptions=True)
            if stop is not None:
                raise RuntimeError(stop)
            stop = "completed_public_trajectory_sample"
            # Stop/join the heartbeat while its client is still open. Closing
            # the client first can create a synthetic shutdown connection error.
            finished.set()
            await asyncio.gather(watch, *([pulse] if pulse else []))
    finally:
        finished.set()
        await asyncio.gather(watch, *([pulse] if pulse else []), return_exceptions=True)
        try:
            current_process_tree()
        except psutil.NoSuchProcess:
            pass
        live = []
        for pid, created_at in tracked.items():
            try:
                process = psutil.Process(pid)
                if process.create_time() == created_at:
                    process.terminate()
                    live.append(process)
            except psutil.NoSuchProcess:
                pass
        _, alive = await asyncio.to_thread(psutil.wait_procs, live, timeout=10)
        for process in alive:
            process.kill()
        if alive:
            await asyncio.to_thread(psutil.wait_procs, alive, timeout=5)
        try:
            await asyncio.to_thread(worker.wait, 10)
        except subprocess.TimeoutExpired:
            worker.kill()
            await asyncio.to_thread(worker.wait, 5)
        log.close()
        still_alive = []
        for pid, created_at in tracked.items():
            try:
                process = psutil.Process(pid)
                if process.create_time() == created_at and process.is_running():
                    still_alive.append(pid)
            except psutil.NoSuchProcess:
                pass
        report = {"purpose": "Bounded local HTTP compatibility and storage proof on public historical trajectories",
                  "stop_reason": stop, "worker_reaped": worker.poll() is not None and not still_alive,
                  "tracked_worker_processes": len(tracked), "remaining_worker_processes": still_alive,
                  "resource_measurement": "Sum of sampled RSS over launcher and recursive server children; shared pages may be counted more than once",
                  "public_trajectories": completed_trajectories, "selected_public_trajectories": len(receipt["files"]),
                  "messages_restored_exactly": message_count, "messages_per_add_limit": args.message_batch,
                  "reranker_enabled": args.model_dir is not None,
                  "evidence_budget_characters": settings.rerank_response_chars if args.model_dir else settings.response_chars,
                  "add_requests": len(operations), "cross_add_pairs_verified": cross_add_pairs,
                  "parallel_client_trajectories": args.parallel_trajectories, "server_processing_slots": 2,
                  "duplicate_add_retries_verified": duplicate_retries,
                  "conflicting_add_retries_rejected_without_replacement": conflicting_retries,
                  "transport_add_requests_including_expected_retries": len(operations) + duplicate_retries + conflicting_retries,
                  "search_requests": len(search_operations), "search_max_ms": max(search_operations, default=None),
                  "role_counts": dict(role_counts), "input_json_bytes": input_bytes,
                  "normalized_source_view_characters": source_chars,
                  "database_bytes": sum(path.stat().st_size for path in settings.data_dir.glob("*.sqlite3")),
                  "peak_worker_rss_bytes": peak_rss, "health_success_samples": len(health_ms),
                  "health_errors": health_errors, "health_failures": health_failures, "add_max_ms": max(operations, default=None),
                  "elapsed_seconds": round(time.perf_counter() - started, 3),
                  "guards": {"worker_rss_mib": args.rss_mib, "database_mib": args.disk_mib, "seconds": args.seconds},
                  "source_sha256": {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                                    for path in sorted((ROOT / "heguang_code").glob("*.py"))},
                  "probe_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  "dataset_revision": receipt["revision"], "sample_receipt_sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
                  "real_model_calls": 0, "official_submissions": 0,
                  "limitations": f"{len(receipt['files'])} selected public historical trajectories, up to {args.parallel_trajectories} parallel client trajectories and two server processing slots, local Windows worker. This is not task resolution, official CAMBench, Full-scale capacity, or cloud validation. Original structured messages are retained alongside explicit derived source views."}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    assert stop == "completed_public_trajectory_sample" and report["worker_reaped"] and health_errors == 0, report
    print(json.dumps({"status": "pass", "messages_restored": message_count, "worker_reaped": True,
                      "database_bytes": report["database_bytes"], "peak_rss_bytes": peak_rss}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=180)
    parser.add_argument("--rss-mib", type=int, default=384)
    parser.add_argument("--disk-mib", type=int, default=512)
    parser.add_argument("--message-batch", type=int, default=25)
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--parallel-trajectories", type=int, default=1)
    parser.add_argument("--retry-first-batch", action="store_true")
    parser.add_argument("--receipt-name", default="trajectory-sample-receipts.json")
    args = parser.parse_args()
    if not 1 <= args.parallel_trajectories <= 8:
        raise ValueError("Choose 1..8 parallel client trajectories")
    if not 1 <= args.message_batch <= 10000:
        raise ValueError("Choose 1..10000 messages per Add")
    if Path(args.receipt_name).name != args.receipt_name:
        raise ValueError("Receipt must be a filename inside --data")
    asyncio.run(run(args))
