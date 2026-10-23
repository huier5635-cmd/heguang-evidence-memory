"""Two stateless local model drafts on public inputs. No model output is applied or run."""
import argparse
import asyncio
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research.local_codegen import prepare, proposed_files
from acquire_local_codegen import MODEL_HASH, MODEL_NAME, file_hash


async def run(args):
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    for name, expected in plan["source_sha256"].items():
        assert file_hash(ROOT / name) == expected, "Frozen research source differs"
    assert plan["public_inputs_verified"] and plan["official_data"] is False
    assert sorted(plan["arm_order"]) == ["with_memory", "without_memory"]
    assert plan["limits"]["generation_requests"] == 2 and plan["automatic_code_execution"] is False
    receipt = json.loads((args.model_dir / plan.get("runtime_receipt", "receipt.json")).read_text(encoding="utf-8"))
    assert receipt["model_sha256"] == MODEL_HASH == plan["model_sha256"]
    assert file_hash(args.model_dir / MODEL_NAME) == MODEL_HASH
    for row in receipt["runtime_files"]:
        assert file_hash(args.model_dir / row["path"]) == row["sha256"]
    assert receipt["runtime_archive_sha256"] == plan["runtime_archive_sha256"]
    assert psutil.virtual_memory().available > plan["limits"].get("minimum_start_available_mib", 7168) * 1024 ** 2, "Insufficient available memory for the bounded 7B pilot"
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "frozen-plan.json").write_bytes(args.plan.read_bytes())
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    key = secrets.token_urlsafe(32)
    environment = {name: value for name, value in os.environ.items() if name.upper() in {
        "SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "COMSPEC", "PATHEXT"}}
    environment["LLAMA_API_KEY"] = key
    server = plan["server"]
    runtime_directory = (args.model_dir / receipt["server_relative_path"]).resolve().parent
    environment["GGML_BACKEND_PATH"] = str(runtime_directory)
    if server.get("gpu_layers", 0):
        assert receipt.get("backend") == "vulkan" and server.get("device", "").startswith("Vulkan")
    assert server.get("repack") is False, "The bounded local pilot disables CPU weight duplication"
    command = [str((args.model_dir / receipt["server_relative_path"]).resolve()),
        "--model", str((args.model_dir / MODEL_NAME).resolve()), "--alias", "local-public-code-pilot", "--host", "127.0.0.1", "--port", str(port),
        "--threads", str(server["threads"]), "--threads-batch", str(server["threads"]),
        "--ctx-size", str(server["context_tokens"]), "--parallel", "1", "--batch-size", "256", "--ubatch-size", "128",
        "--cache-type-k", server["cache_type_k"], "--cache-type-v", server["cache_type_v"], "--flash-attn", "on",
        "--n-gpu-layers", str(server.get("gpu_layers", 0)), "--no-repack", "--no-cache-prompt", "--no-context-shift", "--no-webui", "--log-disable"]
    if server.get("device"):
        command.extend(["--device", server["device"]])
    log = (args.output / "worker.log").open("xb")
    worker = subprocess.Popen(command, env=environment, cwd=runtime_directory, stdout=log, stderr=subprocess.STDOUT,
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    process = psutil.Process(worker.pid)
    created_at = process.create_time()
    if os.name == "nt":
        process.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    (args.output / "worker.json").write_text(json.dumps({"pid": worker.pid, "create_time": created_at,
        "loopback_port": port, "key_persisted": False}, indent=2) + "\n", encoding="utf-8")
    started, peak, requests_started = time.monotonic(), 0, 0
    guard_reason, drafts = None, {}
    lowest_gpu_free, last_gpu_check, last_progress = None, 0, 0
    done = asyncio.Event()
    progress_state = {}
    def progress(stage, **extra):
        if stage is not None:
            progress_state["stage"] = stage
        progress_state.update(extra)
        target = args.output / "progress.json"
        temporary = args.output / "progress.json.tmp"
        temporary.write_text(json.dumps({**progress_state, "controller_pid": os.getpid(), "worker_pid": worker.pid,
            "generation_requests_started": requests_started, "elapsed_seconds": round(time.monotonic() - started, 2),
            "updated_at_unix": time.time()}, indent=2) + "\n", encoding="utf-8")
        temporary.replace(target)
    progress("loading_model")

    async def guard():
        nonlocal peak, guard_reason, lowest_gpu_free, last_gpu_check, last_progress
        while not done.is_set():
            try:
                rss = process.memory_info().rss
            except psutil.NoSuchProcess:
                guard_reason = "worker_exited"
                return
            peak = max(peak, rss)
            limits = plan["limits"]
            now = time.monotonic()
            if server.get("gpu_layers", 0) and now - last_gpu_check >= 2:
                reading = await asyncio.to_thread(subprocess.run, ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                if reading.returncode != 0:
                    raise RuntimeError("GPU resource observer failed")
                free = min(int(value.strip()) for value in reading.stdout.splitlines() if value.strip())
                lowest_gpu_free = min(lowest_gpu_free, free) if lowest_gpu_free is not None else free
                last_gpu_check = now
                if free < limits.get("minimum_gpu_free_mib", 1536):
                    guard_reason = "local_gpu_memory_guard"
            if now - last_progress >= 5:
                progress(None, rss_bytes=rss, gpu_free_mib=lowest_gpu_free)
                last_progress = now
            if rss > limits["rss_mib"] * 1024 ** 2 or psutil.virtual_memory().available < limits["minimum_available_mib"] * 1024 ** 2:
                guard_reason = "local_memory_guard"
            if time.monotonic() - started > limits["total_seconds"]:
                guard_reason = "local_wall_time_guard"
            if guard_reason:
                if worker.poll() is None:
                    worker.terminate()
                return
            await asyncio.sleep(.25)

    async def observe():
        nonlocal guard_reason
        try:
            await guard()
        except Exception:
            guard_reason = "resource_observer_failure"
            if worker.poll() is None:
                worker.terminate()

    watch = asyncio.create_task(observe())
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", headers={"Authorization": "Bearer " + key},
                                     trust_env=False, follow_redirects=False, timeout=900) as client:
            async with asyncio.timeout(90):
                while True:
                    assert worker.poll() is None, "Local model startup failed"
                    try:
                        response = await client.get("/health", timeout=1)
                        if response.status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(.5)
            print(json.dumps({"event": "local_model_ready", "pid": worker.pid, "paid_api_calls": 0}), flush=True)
            progress("ready")
            common = json.loads(plan["messages"]["without_memory"][1]["content"])
            originals = {row["path"]: row["source"] for row in common["unmodified_base_source_files"]}
            for name, expected in plan["allowed_file_sha256"].items():
                assert hashlib.sha256(originals[name].encode()).hexdigest() == expected
            for arm in plan["arm_order"]:
                messages = plan["messages"][arm]
                formatted = await client.post("/apply-template", json={"messages": messages, "add_generation_prompt": True})
                formatted.raise_for_status()
                prompt = formatted.json()["prompt"]
                counted = await client.post("/tokenize", json={"content": prompt, "add_special": True})
                counted.raise_for_status()
                tokens = len(counted.json()["tokens"])
                assert tokens + plan["generation"]["max_tokens"] + 256 <= server["context_tokens"], "Input exceeds declared context; no silent truncation"
                tick = time.monotonic()
                print(json.dumps({"event": "local_draft_started", "arm": arm, "input_tokens": tokens}), flush=True)
                requests_started += 1
                progress("generating", arm=arm, input_tokens=tokens)
                response = await client.post("/v1/chat/completions", json={"messages": messages, **plan["generation"],
                    "model": "local-public-code-pilot", "stream": False, "cache_prompt": False})
                response.raise_for_status()
                value = response.json()
                raw = json.dumps(value, indent=2).encode() + b"\n"
                (args.output / (arm + ".json")).write_bytes(raw)
                content = value["choices"][0]["message"]["content"]
                validation = {"valid_exact_edits": False}
                try:
                    proposed = proposed_files(content, originals)
                    validation = {"valid_exact_edits": True, "changed_files": [name for name in proposed if proposed[name] != originals[name]]}
                except (ValueError, TypeError, KeyError):
                    validation["error"] = "Invalid JSON edits, paths or original snippets; no automatic repair or application"
                drafts[arm] = {"response_sha256": hashlib.sha256(raw).hexdigest(), "input_tokens_preflight": tokens,
                    "usage": value.get("usage"), "finish_reason": value["choices"][0].get("finish_reason"),
                    "seconds": round(time.monotonic() - tick, 3), **validation}
                print(json.dumps({"event": "local_draft_frozen", "arm": arm, **drafts[arm]}), flush=True)
                progress("draft_frozen", arm=arm, response_sha256=drafts[arm]["response_sha256"])
    finally:
        done.set()
        await asyncio.gather(watch, return_exceptions=True)
        if worker.poll() is None:
            worker.terminate()
        try:
            await asyncio.to_thread(worker.wait, 10)
        except subprocess.TimeoutExpired:
            worker.kill()
            await asyncio.to_thread(worker.wait, 5)
        log.close()
        alive = False
        try:
            candidate = psutil.Process(worker.pid)
            alive = candidate.create_time() == created_at and candidate.is_running()
        except psutil.NoSuchProcess:
            pass
        report = {"purpose": __doc__, "complete": len(drafts) == 2 and guard_reason is None,
            "frozen_plan_sha256": hashlib.sha256(args.plan.read_bytes()).hexdigest(), "model_sha256": MODEL_HASH,
            "runtime_archive_sha256": receipt["runtime_archive_sha256"], "drafts": drafts,
            "generation_requests_started": requests_started, "guard_reason": guard_reason,
            "peak_worker_rss_bytes": peak, "worker_reaped": worker.poll() is not None and not alive,
            "lowest_total_device_free_gpu_mib": lowest_gpu_free,
            "gpu_memory_scope": "Whole NVIDIA device including other applications; not per-model allocation",
            "elapsed_seconds": round(time.monotonic() - started, 3), "paid_api_calls": 0,
            "network_destination": "127.0.0.1 only", "model_output_applied": False,
            "coding_tests_executed": False, "official_score": False,
            "limitations": "Public development pilot using a different model and a smaller explicit context budget than AML. Two drafts, not task-resolution scores. The manual case has already been developed; no claim of new blind task selection. Both frozen drafts still require change review and executable tests."}
        (args.output / "result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        progress("terminal", complete=report["complete"], worker_reaped=report["worker_reaped"])
    assert report["complete"] and report["worker_reaped"], report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(run(parser.parse_args()))
