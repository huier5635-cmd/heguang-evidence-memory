"""Bounded synthetic large-Add HTTP memory probe; no relevance or Full-scale claim."""
import argparse
import asyncio
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from heguang_code.config import Settings
from heguang_code.store import Store, digest


async def run(args):
    args.work.mkdir(parents=True, exist_ok=False)
    settings = Settings(data_dir=args.work / "db", api_key="synthetic-large-http-only")
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    env = os.environ.copy()
    env.update({"PYTHONPATH": str(ROOT), "HCM_DATA_DIR": str(settings.data_dir.resolve()),
                "HCM_API_KEY": settings.api_key, "HCM_MAX_CONCURRENCY": "2", "HCM_PROFILE": "rarity"})
    if args.model_dir:
        env["HCM_RERANKER_DIR"] = str(args.model_dir.resolve())
    else:
        env.pop("HCM_RERANKER_DIR", None)
    log = (args.work / "worker.log").open("xb")
    worker = subprocess.Popen([sys.executable, "-m", "uvicorn", "heguang_code.api:create_app", "--factory",
        "--host", "127.0.0.1", "--port", str(port), "--workers", "1", "--no-access-log", "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    tracked, peak, stop = {}, 0, None
    started, done = time.monotonic(), asyncio.Event()
    health_ok, health_errors, responses, verified, bodies = 0, 0, [], 0, []

    def tree():
        parent = psutil.Process(worker.pid)
        processes = [parent, *parent.children(recursive=True)]
        for process in processes:
            tracked[process.pid] = process.create_time()
        return processes

    async def watch():
        nonlocal peak, stop
        try:
            while not done.is_set():
                rss = sum(process.memory_info().rss for process in tree())
                peak = max(peak, rss)
                if rss > args.rss_mib * 1024 ** 2 or time.monotonic() - started > args.seconds:
                    stop = "resource_guard"
                    if worker.poll() is None:
                        worker.terminate()
                    return
                await asyncio.sleep(.025)
        except psutil.NoSuchProcess:
            if not done.is_set():
                stop = "worker_exited"
        except Exception:
            stop = "observer_failed"
            if worker.poll() is None:
                worker.terminate()

    async def heartbeat(client):
        nonlocal health_ok, health_errors
        while not done.is_set():
            try:
                response = await client.get("/health", timeout=2)
                health_ok += response.status_code == 200
                health_errors += response.status_code != 200
            except httpx.HTTPError:
                if stop is None:
                    health_errors += 1
            await asyncio.sleep(.1)

    watch_task, pulse = asyncio.create_task(watch()), None
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=120) as client:
            async with asyncio.timeout(20):
                while True:
                    assert worker.poll() is None
                    try:
                        if (await client.get("/health", timeout=1)).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(.1)
            pulse = asyncio.create_task(heartbeat(client))
            size = args.payload_mib * 1024 ** 2
            line = "def replay_packet(value): return value  # src/protocol.py\n"
            originals = [("large-user-" + str(index), (f"payload_marker_{index}\n" + line * (size // len(line) + 1))[:size])
                         for index in range(args.parallel)]
            for user, content in originals:
                body = json.dumps({"user_id": user, "session_id": "synthetic-large", "request_id": "large-add",
                    "messages": [{"role": "user", "content": content}]}, separators=(",", ":")).encode()
                assert len(body) <= settings.max_body_bytes
                bodies.append(body)
            print(json.dumps({"event": "large_add_started", "parallel": args.parallel,
                              "http_body_bytes_each": [len(body) for body in bodies]}), flush=True)
            results = await asyncio.gather(*(client.post("/add", content=body, headers={
                "Content-Type": "application/json", "Authorization": "Bearer " + settings.api_key}) for body in bodies), return_exceptions=True)
            responses = [row.status_code if isinstance(row, httpx.Response) else type(row).__name__ for row in results]
            if responses == [200] * args.parallel:
                store = Store(settings)
                def restore():
                    for user, content in originals:
                        db = store.connect(user, create=False)
                        try:
                            source = digest([user, "large-add", 0])[:40]
                            text = store.source_span(db, source, 0, len(content), {})
                            assert text == content
                            assert db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
                        finally:
                            db.close()
                    return len(originals)
                verified = await asyncio.to_thread(restore)
            if stop is None:
                stop = "completed" if verified == args.parallel else "request_failed"
            done.set()
            await asyncio.gather(watch_task, *([pulse] if pulse else []))
    finally:
        done.set()
        await asyncio.gather(watch_task, *([pulse] if pulse else []), return_exceptions=True)
        try:
            tree()
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
        remaining = []
        for pid, created_at in tracked.items():
            try:
                process = psutil.Process(pid)
                if process.create_time() == created_at and process.is_running():
                    remaining.append(pid)
            except psutil.NoSuchProcess:
                pass
        report = {"purpose": __doc__, "passed": stop == "completed" and verified == args.parallel and health_errors == 0,
            "stop_reason": stop, "parallel_adds": args.parallel, "source_mib_each": args.payload_mib,
            "http_body_bytes_each": [len(body) for body in bodies], "responses": responses,
            "sources_restored_exactly": verified, "reranker_loaded": args.model_dir is not None,
            "peak_worker_tree_rss_bytes": peak, "health_successes": health_ok, "health_errors": health_errors,
            "worker_reaped": worker.poll() is not None and not remaining, "remaining_processes": remaining,
            "guards": {"rss_mib": args.rss_mib, "seconds": args.seconds},
            "source_sha256": {path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                              for path in sorted((ROOT / "heguang_code").glob("*.py"))},
            "probe_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "elapsed_seconds": round(time.monotonic() - started, 3), "paid_api_calls": 0, "official_runs": 0,
            "limitations": "Highly repetitive synthetic source used to isolate large HTTP/body memory, not unique corpus storage, semantic quality, cloud capacity or official Full scale. RSS is sampled over the worker process tree."}
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ["passed", "stop_reason", "responses", "peak_worker_tree_rss_bytes", "worker_reaped"]}), flush=True)
    assert report["passed"] and report["worker_reaped"], "Large-body gate failed; inspect the saved report"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("work", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--parallel", type=int, choices=[1, 2], default=2)
    parser.add_argument("--payload-mib", type=int, choices=range(1, 61), default=60)
    parser.add_argument("--rss-mib", type=int, default=768)
    parser.add_argument("--seconds", type=int, default=120)
    asyncio.run(run(parser.parse_args()))
