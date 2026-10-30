"""Loopback-only staging entry point; never selects production data or ports."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sys


def prepare():
    release = Path(__file__).resolve().parent
    sys.path.insert(0,str(release))
    manifest_path = release/'runtime-manifest.json'
    raw = manifest_path.read_bytes()
    from manifest_validation import validate_bundle
    _, manifest = validate_bundle(release, hashlib.sha256(raw).hexdigest())
    core = release/'source'
    sys.path.insert(0,str(core))
    import heguang_code
    from heguang_code.config import Settings
    from observed_api import create_app
    from request_lifecycle import CompleteLargeRequest
    from manifest_validation import validate_model
    assert Path(heguang_code.__file__).resolve().is_relative_to(core.resolve())
    assert heguang_code.__version__ == '0.2.0.dev25'
    assert os.geteuid() != 0
    identity = hashlib.sha256(raw).hexdigest()[:12]
    data = Path('/var/lib/heguang-coding-stage-dev25diag-'+identity)
    assert data.is_dir() and not data.is_symlink()
    original = Settings.from_env()
    assert original.reranker_dir is not None
    validate_model(original.reranker_dir, json.loads((release/"model-receipt.json").read_text("utf-8")))
    # Replace data/config in Python. systemd EnvironmentFile overrides can
    # otherwise silently select the old application's persistent directory.
    settings = replace(original,data_dir=data,profile='rarity',chunk_chars=3600,overlap_chars=360,
        block_chars=8192,candidates=300,response_chars=32000,session_penalty=0.0,
        reranker_weight=4.,reranker_threads=1,source_selection='coalesce',rerank_response_chars=128000,
        max_concurrency=1,max_body_bytes=128*1024**2,large_body_bytes=16*1024**2,retention_days=29)
    return CompleteLargeRequest(create_app(settings))


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(prepare(),host='127.0.0.1',port=8753,workers=1,access_log=False,log_level='warning')
