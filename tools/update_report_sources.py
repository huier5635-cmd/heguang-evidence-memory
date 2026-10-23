"""Map every recorded research/code hash to current source or an exact archived snapshot."""
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]


def run():
    archived = {}
    for path in sorted((ROOT / "history").glob("*.zip")):
        with ZipFile(path) as archive:
            for name in archive.namelist():
                if not name.endswith("/"):
                    archived.setdefault((name.replace("\\", "/"), hashlib.sha256(archive.read(name)).hexdigest()), path.name)
    mapped = {}
    for path in sorted((ROOT / "results").glob("*.json")):
        report = json.loads(path.read_text(encoding="utf-8"))
        hashes = {}
        for field in ("source_sha256", "test_sha256", "research_sha256", "research_source_sha256"):
            hashes.update({name.replace("\\", "/"): value for name, value in report.get(field, {}).items()})
        if "probe_source_sha256" in report:
            hashes["tools/trajectory_http_probe.py"] = report["probe_source_sha256"]
        if "probe_sha256" in report:
            hashes["tools/large_payload_probe.py"] = report["probe_sha256"]
        locations = {}
        for name, expected in hashes.items():
            current = ROOT / name
            if current.is_file() and hashlib.sha256(current.read_bytes()).hexdigest() == expected:
                locations[name] = "current"
            else:
                locations[name] = archived.get((name, expected), "source_snapshot_not_available")
        mapped[path.name] = {"has_code_hashes": bool(hashes),
            "all_recorded_sources_available": bool(hashes) and all(value != "source_snapshot_not_available" for value in locations.values()),
            "sources": locations}
    (ROOT / "history/report-sources.json").write_text(json.dumps({
        "purpose": "Every recorded CODE file hash maps to its exact source snapshot. Dataset/model/oracle hashes remain separate input receipts. Current means this package.",
        "reports": mapped}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"mapped_reports": len(mapped), "reports_with_incomplete_code_snapshots": [name for name, row in mapped.items()
        if row["has_code_hashes"] and not row["all_recorded_sources_available"]]}))


if __name__ == "__main__":
    run()
