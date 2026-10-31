"""Same tested API/settings, separate fresh data and a loopback listener."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sys

def prepare():
    folder=Path(__file__).resolve().parent;sys.path.insert(0,str(folder))
    from public_manifest import validate,parent_runtime,PARENT
    expected=os.environ['QIANORA_PUBLIC_MANIFEST_SHA'];validate(folder,expected)
    raw=(folder/'public-manifest.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest()==expected and os.geteuid()!=0
    parent_runtime();sys.path.insert(0,str(PARENT/'source'))
    import heguang_code
    from heguang_code.config import Settings
    from observed_api import create_app
    from request_lifecycle import CompleteLargeRequest
    from manifest_validation import validate_model
    from public_unit import locations
    assert heguang_code.__version__=='0.2.0.dev25' and Path(heguang_code.__file__).resolve().is_relative_to(PARENT/'source')
    data=Path(locations(raw)['data']);assert data.is_dir() and not data.is_symlink()
    original=Settings.from_env();assert original.reranker_dir is not None
    validate_model(original.reranker_dir,json.loads((PARENT/'model-receipt.json').read_text('utf-8')))
    settings=replace(original,data_dir=data,profile='rarity',chunk_chars=3600,overlap_chars=360,
        block_chars=8192,candidates=300,response_chars=32000,session_penalty=0.0,
        reranker_weight=4.,reranker_threads=1,source_selection='coalesce',rerank_response_chars=128000,
        max_concurrency=1,max_body_bytes=128*1024**2,large_body_bytes=16*1024**2,retention_days=29)
    return CompleteLargeRequest(create_app(settings))

if __name__=='__main__':
    import uvicorn
    uvicorn.run(prepare(),host='127.0.0.1',port=8755,workers=1,access_log=False,log_level='warning')
