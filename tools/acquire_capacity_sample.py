"""Download a larger pinned, temporally earlier public-trajectory capacity sample."""
import argparse
import concurrent.futures
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

import pyarrow.parquet as pq

REPO = "Tongyi-ConvAI/VibeMemBench"
REVISION = "11164976b4f920cdb0d0cd570ca572e49acea1e8"


def run(args):
    url = f"https://huggingface.co/api/datasets/{REPO}/revision/{REVISION}?blobs=true"
    for attempt in range(5):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                metadata = json.load(response)
            break
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 4:
                raise
            time.sleep(min(60, int(error.headers.get("Retry-After", "0")) or 2 ** attempt))
        except (TimeoutError, ConnectionError, urllib.error.URLError):
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)
    assert metadata["sha"] == REVISION
    targets = pq.read_table(args.data / "VibeMemBench/test.parquet", columns=["instance_id", "repo"]).to_pylist()
    minimum, target_ids = {}, {row["instance_id"] for row in targets}
    for row in targets:
        repo = row["repo"].replace("/", "__")
        number = int(row["instance_id"].rsplit("-", 1)[1])
        minimum[repo] = min(number, minimum.get(repo, number))
    grouped = defaultdict(list)
    for entry in metadata["siblings"]:
        parts = entry["rfilename"].split("/")
        if len(parts) != 5 or parts[0] != "history" or not parts[-1].endswith(".traj.json"):
            continue
        _, language, repo, iid, _ = parts
        if repo in minimum and iid not in target_ids and int(iid.rsplit("-", 1)[1]) < minimum[repo]:
            grouped[language].append(entry)
    chosen = []
    for language, entries in sorted(grouped.items()):
        counts, used = defaultdict(int), 0
        for entry in sorted(entries, key=lambda item: hashlib.sha256(("capacity-20261023:" + item["rfilename"]).encode()).hexdigest()):
            repository = entry["rfilename"].split("/")[2]
            if counts[repository] >= 3:
                continue
            chosen.append(entry)
            counts[repository] += 1
            used += 1
            if used == args.per_language:
                break
    total = sum(entry["size"] for entry in chosen)
    assert total <= args.download_mib * 1024 ** 2
    assert all(entry["size"] <= 32 * 1024 ** 2 for entry in chosen)
    plan = {"dataset": REPO, "revision": REVISION, "selection_seed": "capacity-20261023",
            "selection": "Up to N files per language, max 3 per repository, SHA order; earlier than every target in that repository; all target trajectories excluded",
            "per_language_limit": args.per_language, "planned_files": len(chosen), "planned_bytes": total,
            "languages": sorted({entry["rfilename"].split("/")[1] for entry in chosen}),
            "official_submissions": 0, "paid_api_calls": 0}
    (args.data / "capacity-sample-plan.json").write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"event": "capacity_download_planned", "files": len(chosen), "bytes": total}), flush=True)

    def download(entry):
        name = entry["rfilename"]
        local = "trace-capacity/" + hashlib.sha256(name.encode()).hexdigest()[:20] + ".json"
        path = args.data / "VibeMemBench" / local
        path.parent.mkdir(parents=True, exist_ok=True)
        if len(str(path.resolve())) > 240:
            raise RuntimeError("Choose a shorter public --data path")
        url = f"https://huggingface.co/datasets/{REPO}/resolve/{REVISION}/{urllib.parse.quote(name)}"
        if not path.exists():
            for attempt in range(5):
                try:
                    with urllib.request.urlopen(url, timeout=60) as response:
                        body = response.read(entry["size"] + 1)
                    assert len(body) == entry["size"]
                    temporary = path.with_suffix(".part")
                    temporary.write_bytes(body)
                    temporary.replace(path)
                    break
                except urllib.error.HTTPError as error:
                    if error.code not in (429, 500, 502, 503, 504) or attempt == 4:
                        raise
                    time.sleep(min(60, int(error.headers.get("Retry-After", "0")) or 2 ** attempt))
                except (TimeoutError, ConnectionError, urllib.error.URLError):
                    if attempt == 4:
                        raise
                    time.sleep(2 ** attempt)
        body = path.read_bytes()
        expected = entry.get("lfs", {}).get("sha256")
        valid = hashlib.sha256(body).hexdigest() == expected if expected else hashlib.sha1(f"blob {len(body)}\0".encode() + body).hexdigest() == entry["blobId"]
        assert len(body) == entry["size"] and valid
        return {"path": name, "local_path": local, "url": url, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        sample = []
        for result in pool.map(download, chosen):
            sample.append(result)
            if len(sample) % 20 == 0:
                print(json.dumps({"event": "capacity_files_verified", "completed": len(sample), "total": len(chosen)}), flush=True)
    receipt = {**plan, "files": sample, "bytes": sum(row["bytes"] for row in sample), "real_model_calls": 0}
    (args.data / "capacity-sample-receipts.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"complete": True, "files": len(sample), "bytes": receipt["bytes"]}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--per-language", type=int, default=20)
    parser.add_argument("--download-mib", type=int, default=512)
    args = parser.parse_args()
    assert 1 <= args.per_language <= 100
    run(args)
