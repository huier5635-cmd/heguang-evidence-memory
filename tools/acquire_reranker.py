"""Acquire an Apache-2.0 cross-encoder reranker at a pinned revision for local CPU research."""
import argparse
import hashlib
import json
import urllib.parse
import urllib.request
from pathlib import Path

REPO = "cross-encoder/ms-marco-MiniLM-L6-v2"
REVISION = "233902d25c440f23af6f7d6e94d2946bac0bee0a"
MODEL_SHA256 = "c80a8b34256ea453093d612e3ac48d3d965a0c0a48c7906709af8b8e28461bf9"
FILES = ["README.md", "onnx/model_quint8_avx2.onnx", "tokenizer.json", "tokenizer_config.json",
         "special_tokens_map.json", "config.json"]


def run(args):
    args.model_dir.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(f"https://huggingface.co/api/models/{REPO}/revision/{REVISION}?blobs=true", timeout=30) as response:
        metadata = json.load(response)
    if metadata["sha"] != REVISION or metadata["cardData"]["license"] != "apache-2.0":
        raise RuntimeError("Unexpected public reranker revision/license")
    siblings = {row["rfilename"]: row for row in metadata["siblings"]}
    receipts = []
    for filename in FILES:
        entry = siblings[filename]
        if entry["size"] > 50 * 1024 ** 2:
            raise RuntimeError("Model exceeds download size guard")
        path = args.model_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        url = f"https://huggingface.co/{REPO}/resolve/{REVISION}/{urllib.parse.quote(filename)}"
        if not path.exists():
            temporary = path.with_suffix(path.suffix + ".part")
            size = 0
            with urllib.request.urlopen(url, timeout=60) as response, temporary.open("wb") as target:
                while block := response.read(1024 ** 2):
                    size += len(block)
                    if size > entry["size"]:
                        raise RuntimeError("Encoder file exceeds expected length")
                    target.write(block)
            if size != entry["size"]:
                raise RuntimeError("Incomplete reranker download")
            temporary.replace(path)
        size = path.stat().st_size
        sha256 = hashlib.sha256()
        git_hash = hashlib.sha1(f"blob {size}\0".encode())
        with path.open("rb") as source:
            while block := source.read(1024 ** 2):
                sha256.update(block)
                git_hash.update(block)
        expected = entry.get("lfs", {}).get("sha256")
        if size != entry["size"] or (sha256.hexdigest() != expected if expected else git_hash.hexdigest() != entry["blobId"]):
            raise RuntimeError("Encoder file checksum differs from pinned repository")
        if filename == "onnx/model_quint8_avx2.onnx" and sha256.hexdigest() != MODEL_SHA256:
            raise RuntimeError("Unexpected ONNX model checksum")
        receipts.append({"path": filename, "bytes": size, "sha256": sha256.hexdigest(), "url": url})
        print(json.dumps({"event": "verified_reranker_file", "file": filename, "bytes": size}), flush=True)
    report = {"repository": REPO, "revision": REVISION, "license": "Apache-2.0 (upstream model card)",
              "files": receipts, "public_data_uploaded": False, "paid_api_calls": 0}
    (args.model_dir / "receipt.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    run(parser.parse_args())
