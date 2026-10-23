"""Acquire a pinned optional LOCAL public-case model, never a memory-service component."""
import argparse
import hashlib
import json
import stat
import time
import urllib.error
import urllib.request
from pathlib import Path
from zipfile import ZipFile

MODEL_REPO = "Qwen/Qwen2.5-Coder-7B-Instruct-GGUF"
MODEL_REV = "13fb94bfda8c8cf22497dc57b78f391a9acb426a"
MODEL_NAME = "qwen2.5-coder-7b-instruct-q4_k_m.gguf"
MODEL_SIZE = 4683073536
MODEL_HASH = "509287f78cb4d4cf6b3843734733b914b2c158e43e22a7f4bf5e963800894d3c"
RUNTIME_URL = "https://github.com/ggml-org/llama.cpp/releases/download/b11669/llama-b11669-bin-win-cpu-x64.zip"
RUNTIME_HASH = "37b3efcf962847ca869745fd4ccf7f5e7e8dacfeff9fa3523b4ca8cdc66d632b"
VULKAN_URL = "https://github.com/ggml-org/llama.cpp/releases/download/b11669/llama-b11669-bin-win-vulkan-x64.zip"
VULKAN_HASH = "2c07574e7bde71321cb785282b20cc821e951132149eeceeea32ddebdbf0598e"


def file_hash(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def download(url, target, expected, limit, seconds=900, size=None):
    if target.exists():
        assert file_hash(target) == expected, "Existing public artifact checksum mismatch"
        return
    partial = target.with_name(target.name + ".partial")
    started, announced = time.monotonic(), -1
    for attempt in range(5):
        offset = partial.stat().st_size if partial.exists() else 0
        request = urllib.request.Request(url, headers={"Range": f"bytes={offset}-"} if offset else {})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                if offset:
                    assert response.status == 206 and response.headers.get("Content-Range", "").startswith(f"bytes {offset}-"), "Server did not confirm exact byte resume"
                advertised = int(response.headers.get("Content-Length", "0"))
                assert offset + advertised <= limit
                with partial.open("ab" if offset else "xb") as sink:
                    while True:
                        if time.monotonic() - started > seconds:
                            raise RuntimeError("Download wall-time limit; preserve partial file for exact resume")
                        block = response.read(4 * 1024 ** 2)
                        if not block:
                            break
                        offset += len(block)
                        assert offset <= limit
                        sink.write(block)
                        checkpoint = offset // (256 * 1024 ** 2)
                        if checkpoint != announced:
                            print(json.dumps({"event": "public_artifact_download", "file": target.name, "bytes": offset}), flush=True)
                            announced = checkpoint
            if size is not None:
                assert partial.stat().st_size == size, "Published public model size differs"
            assert file_hash(partial) == expected, "Published public artifact checksum differs"
            partial.rename(target)
            return
        except urllib.error.HTTPError as error:
            if error.code not in {429, 500, 502, 503, 504} or attempt == 4:
                raise
            delay = min(60, int(error.headers.get("Retry-After", "60")))
            time.sleep(max(1, delay))
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError("Public download did not complete")


def run(root, backend="cpu"):
    root.mkdir(parents=True, exist_ok=True)
    runtime_url, expected = (VULKAN_URL, VULKAN_HASH) if backend == "vulkan" else (RUNTIME_URL, RUNTIME_HASH)
    archive_path = root / runtime_url.rsplit("/", 1)[1]
    download(runtime_url, archive_path, expected, 256 * 1024 ** 2, seconds=180)
    runtime = root / ("runtime-vulkan-b11669" if backend == "vulkan" else "runtime-b11669")
    runtime.mkdir(exist_ok=True)
    with ZipFile(archive_path) as archive:
        assert sum(member.file_size for member in archive.infolist()) < 768 * 1024 ** 2
        for member in archive.infolist():
            destination = (runtime / member.filename).resolve()
            assert destination.is_relative_to(runtime.resolve()) and not stat.S_ISLNK(member.external_attr >> 16)
            if member.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                body = archive.read(member)
                if destination.exists():
                    assert destination.read_bytes() == body
                else:
                    destination.write_bytes(body)
    url = f"https://huggingface.co/{MODEL_REPO}/resolve/{MODEL_REV}/{MODEL_NAME}"
    download(url, root / MODEL_NAME, MODEL_HASH, 5 * 1024 ** 3, size=MODEL_SIZE)
    for name in ("LICENSE", "README.md"):
        path = root / name
        if not path.exists():
            with urllib.request.urlopen(f"https://huggingface.co/{MODEL_REPO}/resolve/{MODEL_REV}/{name}", timeout=30) as response:
                body = response.read(256 * 1024 + 1)
            assert len(body) <= 256 * 1024
            path.write_bytes(body)
    executables = list(runtime.rglob("llama-server.exe"))
    assert len(executables) == 1
    receipt = {"purpose": __doc__, "model_repo": MODEL_REPO, "model_revision": MODEL_REV,
               "model_file": MODEL_NAME, "model_bytes": MODEL_SIZE, "model_sha256": MODEL_HASH,
               "model_license": "Apache-2.0", "model_context_tokens": 32768,
               "runtime_release": "b11669 (v0.7.0 commit)", "runtime_url": runtime_url, "backend": backend,
               "runtime_archive_sha256": expected, "server_relative_path": executables[0].relative_to(root).as_posix(),
               "runtime_files": [{"path": path.relative_to(root).as_posix(), "sha256": file_hash(path)}
                                 for path in sorted(runtime.rglob("*")) if path.is_file()],
               "paid_api_calls": 0, "memory_service_component": False, "official_submissions": 0}
    (root / ("receipt-vulkan.json" if backend == "vulkan" else "receipt.json")).write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"acquired_and_verified": True, "model_bytes": MODEL_SIZE,
                      "memory_service_component": False, "paid_api_calls": 0}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--backend", choices=["cpu", "vulkan"], default="cpu")
    args = parser.parse_args()
    run(args.model_dir, args.backend)
