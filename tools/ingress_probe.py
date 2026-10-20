"""Local-only HTTP upload admission probe. Never supply private evaluation data.

Starts an isolated Uvicorn on 127.0.0.1, holds one synthetic Add, and streams
more synthetic requests. Add is deliberately mocked: this measures ingress
backpressure, not database/model throughput, RSS or Full capacity.
"""
import argparse
import asyncio
import hashlib
import json
import socket
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path


async def probe(args):
    sys.path.insert(0, str(args.source.resolve()))
    import httpx
    import uvicorn
    from aml_memory.api import create_app
    from aml_memory.config import Settings

    with tempfile.TemporaryDirectory(prefix="memory-ingress-") as folder:
        app = create_app(Settings(data_dir=Path(folder), api_key="local-synthetic-test", max_concurrency=1))
        held, release = asyncio.Event(), asyncio.Event()
        arrived = {str(i): asyncio.Event() for i in range(args.pending)}
        reads = {str(i): 0 for i in range(args.pending)}

        async def hold_add(req):
            if req.request_id == "held":
                held.set()
                await release.wait()

        app.state.engine.add = hold_add

        async def measured(scope, receive, send):
            label = dict(scope.get("headers", [])).get(b"x-probe", b"").decode()
            if label in arrived:
                arrived[label].set()

                async def counted_receive():
                    message = await receive()
                    if message["type"] == "http.request":
                        reads[label] += len(message.get("body", b""))
                    return message

                await app(scope, counted_receive, send)
            else:
                await app(scope, receive, send)

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(measured, log_level="critical", access_log=False, lifespan="on"))
        serving = asyncio.create_task(server.serve(sockets=[sock]))
        tasks = []
        first = None
        report = None
        try:
            async with asyncio.timeout(5):
                while not server.started:
                    if serving.done():
                        await serving
                        raise RuntimeError("Local HTTP probe did not start")
                    await asyncio.sleep(0.01)
            headers = {"Authorization": "Bearer local-synthetic-test", "Content-Type": "application/json"}
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", headers=headers, timeout=30,
                                         trust_env=False) as http:
                body = {"request_id": "held", "user_id": "synthetic", "session_id": "probe",
                        "messages": [{"role": "user", "content": "Synthetic hold."}]}
                first = asyncio.create_task(http.post("/add", json=body))
                await asyncio.wait_for(held.wait(), 5)

                async def stream(index):
                    yield ('{"request_id":"queued-' + str(index) + '","user_id":"synthetic",'
                           '"session_id":"probe","messages":[{"role":"user","content":"').encode()
                    chunk = b"x" * 65536
                    for _ in range(args.body_mib * 16):
                        yield chunk
                    yield b'"}]}'

                tracemalloc.start()
                started = time.monotonic()
                for i in range(args.pending):
                    tasks.append(asyncio.create_task(http.post("/add", content=stream(i), headers={"X-Probe": str(i)})))
                await asyncio.wait_for(asyncio.gather(*(event.wait() for event in arrived.values())), 10)
                # Observe until receive counts stop changing. A quiet interval is
                # the stalled-queue observation, not a throughput measurement.
                stable_since = time.monotonic()
                previous = -1
                async with asyncio.timeout(10):
                    while True:
                        current = sum(reads.values())
                        if current != previous:
                            previous, stable_since = current, time.monotonic()
                        if time.monotonic() - stable_since >= 0.25:
                            break
                        await asyncio.sleep(0.05)
                health_start = time.perf_counter()
                health = await asyncio.wait_for(http.get("/health"), 2)
                health_ms = (time.perf_counter() - health_start) * 1000
                current, peak = tracemalloc.get_traced_memory()
                tracemalloc.stop()
                report = {"source_version": health.json()["version"], "transport": "local TCP HTTP/1.1 Uvicorn",
                          "active_adds_held": 1, "max_concurrency": 1, "queued_uploads": args.pending,
                          "synthetic_text_bytes_per_upload": args.body_mib * 1024 * 1024,
                          "queued_body_bytes_consumed_before_release": sum(reads.values()),
                          "queued_uploads_consumed": sum(value > 0 for value in reads.values()),
                          "python_traced_current_bytes": current, "python_traced_peak_bytes": peak,
                          "health_status_while_busy": health.status_code, "health_ms_while_busy": round(health_ms, 3),
                          "observation_seconds": round(time.monotonic() - started, 3)}
                release.set()
                responses = await asyncio.wait_for(asyncio.gather(first, *tasks), 30)
                assert all(response.status_code == 200 for response in responses)
                report["completed_adds_after_release"] = len(responses)
                report["all_http_200"] = True
        finally:
            if tracemalloc.is_tracing():
                tracemalloc.stop()
            release.set()
            for task in [first, *tasks]:
                if task and not task.done():
                    task.cancel()
            await asyncio.gather(*(task for task in [first, *tasks] if task), return_exceptions=True)
            server.should_exit = True
            try:
                await asyncio.wait_for(serving, 5)
            finally:
                sock.close()
                await app.state.engine.models.close()

        report.update({"real_model_calls": 0, "official_runs": 0, "production_changed": False,
                       "api_source_sha256": hashlib.sha256((args.source / "aml_memory/api.py").read_bytes()).hexdigest(),
                       "limitations": "Synthetic ingress only; Add implementation mocked. Python traced allocations are not RSS, process total memory, reverse-proxy behavior or Full capacity."})
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--pending", type=int, default=6, choices=range(1, 17))
    parser.add_argument("--body-mib", type=int, default=4, choices=range(1, 17))
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(probe(parser.parse_args()))
