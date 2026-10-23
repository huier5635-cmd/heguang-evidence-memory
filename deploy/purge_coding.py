"""Coding-only retention, independent of the API process; never reads an HTTP key."""
from pathlib import Path

from heguang_code.config import Settings
from heguang_code.store import Store


if __name__ == "__main__":
    root = Path("/var/lib/heguang-coding")
    if not root.is_dir() or root.is_symlink():
        raise SystemExit("Expected dedicated Coding data directory")
    store = Store(Settings(data_dir=root, api_key="unused-local-retention-task", retention_days=29))
    store.purge_expired()
    print("Coding retention completed; 29-day policy; no request content logged")
