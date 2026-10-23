"""Download pinned public development inputs only; no evaluation credentials needed."""
import argparse
import concurrent.futures
import hashlib
import json
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
DATASETS = {
    "jiayuanz3/SWEContextBench": "12c65bd15e2559bc808065565e941ee7bbbd008f",
    "Tongyi-ConvAI/VibeMemBench": "11164976b4f920cdb0d0cd570ca572e49acea1e8",
}
FILES = {
    "jiayuanz3/SWEContextBench": ["README.md", "data/SWEContextBench_Experience.parquet",
        "data/SWEContextBench_Related.parquet", "data/SWEContextBench_Relationship.parquet"],
    "Tongyi-ConvAI/VibeMemBench": ["README.md", "test.parquet"],
}


def read_url(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Heguang-public-research/0.1"})
    with urllib.request.urlopen(request, timeout=90) as response:
        body = response.read(20 * 1024 ** 2 + 1)
    if len(body) > 20 * 1024 ** 2:
        raise RuntimeError("Public input exceeds per-file download guard")
    return body


def main(args):
    args.data.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((ROOT / "data/pinned-inputs.json").read_text(encoding="utf-8"))
    expected = {(row["dataset"], row["path"]): row for row in manifest["files"]}
    metadata = {}
    for repo, revision in DATASETS.items():
        path = args.data / (repo.split("/")[-1] + "-metadata.json")
        if path.exists():
            value = json.loads(path.read_text(encoding="utf-8"))
        else:
            value = json.loads(read_url(f"https://huggingface.co/api/datasets/{repo}/revision/{revision}"))
            path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        if value["sha"] != revision:
            raise RuntimeError("Public dataset revision differs from the recorded study")
        metadata[repo] = value

    def download(task):
        repo, name, local = task
        if name not in {item["rfilename"] for item in metadata[repo]["siblings"]}:
            raise RuntimeError("Public filename absent from pinned dataset metadata")
        path = args.data / repo.split("/")[-1] / local
        path.parent.mkdir(parents=True, exist_ok=True)
        if len(str(path.resolve())) > 240:
            raise RuntimeError("Choose a shorter --data directory for Windows-compatible paths")
        url = f"https://huggingface.co/datasets/{repo}/resolve/{DATASETS[repo]}/{urllib.parse.quote(name)}"
        if not path.exists():
            temporary = path.with_suffix(path.suffix + ".part")
            temporary.write_bytes(read_url(url))
            temporary.replace(path)
        body = path.read_bytes()
        frozen = expected[(repo, name)]
        if len(body) != frozen["bytes"] or hashlib.sha256(body).hexdigest() != frozen["sha256"]:
            raise RuntimeError("Public input differs from the pinned input digest")
        return {"dataset": repo, "revision": DATASETS[repo], "path": name,
                "local_path": local, "url": url, "bytes": len(body),
                "sha256": hashlib.sha256(body).hexdigest()}

    tasks = [(repo, name, name) for repo, names in FILES.items() for name in names]
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(download, tasks))
    def save_receipt(path, value, rows):
        if path.exists():
            old = json.loads(path.read_text(encoding="utf-8"))
            old_rows = old if isinstance(old, list) else old["files"]
            fields = ("path", "sha256", "bytes")
            canonical = lambda items: sorted(tuple(row[field] for field in fields) for row in items)
            if canonical(old_rows) != canonical(rows):
                raise RuntimeError("Existing acquisition receipt differs; choose a fresh --data directory")
        else:
            path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    save_receipt(args.data / "download-receipts.json", receipts, receipts)
    if args.skip_trajectories:
        print(json.dumps({"pinned_files": len(receipts), "models_called": 0}))
        return
    repo = "Tongyi-ConvAI/VibeMemBench"
    targets = pq.read_table(args.data / "VibeMemBench/test.parquet", columns=["instance_id", "repo"]).to_pylist()
    minimum = {}
    target_ids = {row["instance_id"] for row in targets}
    for row in targets:
        key = row["repo"].replace("/", "__")
        number = int(row["instance_id"].rsplit("-", 1)[1])
        minimum[key] = min(number, minimum.get(key, number))
    eligible = defaultdict(list)
    for item in metadata[repo]["siblings"]:
        parts = item["rfilename"].split("/")
        if len(parts) == 5 and parts[0] == "history" and parts[-1].endswith(".traj.json"):
            _, language, repository, iid, _ = parts
            if repository in minimum and iid not in target_ids and int(iid.rsplit("-", 1)[1]) < minimum[repository]:
                eligible[language].append(item["rfilename"])
    chosen = []
    for language, names in sorted(eligible.items()):
        repositories = set()
        for name in sorted(names, key=lambda value: hashlib.sha256(("sample-20261023:" + value).encode()).hexdigest()):
            repository = name.split("/")[2]
            if repository not in repositories:
                chosen.append((repo, name, "trace-sample/" + hashlib.sha256(name.encode()).hexdigest()[:20] + ".json"))
                repositories.add(repository)
            if len(repositories) == 3:
                break
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        sample = list(pool.map(download, chosen))
    report = {"dataset": repo, "revision": DATASETS[repo],
              "selection": "Up to three distinct repositories per language, SHA256 order; earlier than every target in that repository, target trajectories excluded",
              "real_model_calls": 0, "files": sample, "bytes": sum(row["bytes"] for row in sample)}
    save_receipt(args.data / "trajectory-sample-receipts.json", report, sample)
    print(json.dumps({"pinned_files": len(receipts), "public_historical_trajectories": len(sample), "bytes": report["bytes"], "models_called": 0}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("work/public"))
    parser.add_argument("--skip-trajectories", action="store_true")
    main(parser.parse_args())
