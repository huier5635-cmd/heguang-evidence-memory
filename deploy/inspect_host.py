"""Read-only Linux preflight. Never reads credentials or changes the Textual service."""
import hashlib
import json
import platform
import shutil
import socket
import sqlite3
import sys
import urllib.request
from pathlib import Path


def inspect():
    info = {"platform": platform.platform(), "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version, "mutations": 0, "full_allowed": False}
    info["python_ok"] = sys.version_info >= (3, 12)
    info["sqlite_ok"] = sqlite3.sqlite_version_info >= (3, 43)
    database = sqlite3.connect(":memory:")
    try:
        database.execute("CREATE VIRTUAL TABLE probe USING fts5(text,content='',contentless_delete=1)")
        info["fts5_contentless_delete_ok"] = True
    except sqlite3.Error:
        info["fts5_contentless_delete_ok"] = False
    finally:
        database.close()
    if sys.platform == "linux":
        memory = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
        info["mem_available_bytes"] = int(memory["MemAvailable"].split()[0]) * 1024
        info["disk_free_bytes"] = shutil.disk_usage("/opt").free
        info["smoke_resource_headroom_ok"] = info["mem_available_bytes"] >= 700 * 1024 ** 2 and info["disk_free_bytes"] >= 3 * 1024 ** 3
        caddy = Path("/etc/caddy/Caddyfile")
        info["existing_caddy_config_sha256"] = hashlib.sha256(caddy.read_bytes()).hexdigest() if caddy.is_file() else None
        for name in ("/etc/systemd/system/heguang-memory.service", "/etc/systemd/system/heguang-coding.service"):
            path = Path(name)
            info[path.name + "_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        try:
            with urllib.request.urlopen("http://127.0.0.1:8751/health", timeout=5) as response:
                health = json.load(response)
            info["textual_health"] = {key: health.get(key) for key in ("status", "version", "mode")}
        except Exception as error:
            info["textual_health_error_type"] = type(error).__name__
    with socket.socket() as listener:
        try:
            listener.bind(("127.0.0.1", 8752))
            info["coding_port_available"] = True
        except OSError:
            info["coding_port_available"] = False
    info["full_scale_capacity_verified"] = False
    return info


if __name__ == "__main__":
    print(json.dumps(inspect(), indent=2))
