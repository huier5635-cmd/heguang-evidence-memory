"""Emit public, secret-free provenance. Does not read runtime data or environment."""
import hashlib
import importlib.metadata
import json
from pathlib import Path


root = Path(__file__).resolve().parents[1]
files = {}
for folder in (root / "aml_memory", root / "tools"):
    for path in sorted(folder.glob("*.py")):
        files[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
print(json.dumps({"version": "0.1.0", "source_sha256": files,
                  "dependencies": {name: importlib.metadata.version(name) for name in ("fastapi", "uvicorn", "httpx", "numpy")},
                  "academic_embedding": "text-embedding-v4", "academic_llm": "gpt-4o-mini",
                  "default_mode": "lexical", "formal_submission": False}, indent=2))

