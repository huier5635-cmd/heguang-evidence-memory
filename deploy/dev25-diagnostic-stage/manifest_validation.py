"""Validate bound staging files and existing public model assets without secrets."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def relative_file(value):
    if (not isinstance(value,str) or not value or '\\' in value or ':' in value
            or any(ord(c)<32 or ord(c)==127 for c in value)):
        raise ValueError('Invalid bundle path')
    p=PurePosixPath(value)
    if p.is_absolute() or any(x.casefold() in {'','.','..','.git','.env'} or x.endswith((' ','.')) for x in value.split('/')):
        raise ValueError('Invalid bundle path')
    return p


def regular_bundle_file(root,name):
    root=Path(root).resolve();relative=relative_file(name)
    path=root
    for part in relative.parts:
        path=path/part
        if path.is_symlink():
            raise ValueError('Symlink in bundle')
    path.resolve().relative_to(root)
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError('Bundle entry is not regular')
    return path


def validate_bundle(root,expected_manifest):
    if not re.fullmatch('[0-9a-f]{64}',expected_manifest):
        raise ValueError('Explicit manifest digest required')
    root=Path(root).resolve();path=regular_bundle_file(root,'runtime-manifest.json')
    if sha(path)!=expected_manifest:
        raise ValueError('Manifest changed')
    raw=path.read_bytes();manifest=json.loads(raw)
    if manifest.get('core_version')!='0.2.0.dev25' or manifest.get('runtime_extension')!='complete-large-asgi-cycle1':
        raise ValueError('Unreviewed staging version')
    files=manifest['files_sha256']
    if not isinstance(files,dict) or not 24<=len(files)<=40:
        raise ValueError('Unexpected bundle inventory')
    for name,digest in files.items():
        if not re.fullmatch('[0-9a-f]{64}',digest) or sha(regular_bundle_file(root,name))!=digest:
            raise ValueError('Bound bundle file changed')
    core={name:digest for name,digest in files.items() if name.startswith('source/heguang_code/')}
    if len(core)!=20 or any('/' in name.removeprefix('source/heguang_code/') or not name.endswith('.py') for name in core):
        raise ValueError('Unexpected core module inventory')
    required={'stage_runner.py','stage_unit.py','observed_api.py','request_lifecycle.py',
              'manifest_validation.py','install_stage.py','server_probe.py','pinned_array.py',
              'requirements.txt','model-receipt.json'}
    if set(files)-set(core)!=required:
        raise ValueError('Unexpected staging helper inventory')
    return raw,manifest


def validate_review(path,expected_review,manifest,expected_manifest):
    path=Path(path)
    if path.is_symlink() or not path.is_file() or not re.fullmatch('[0-9a-f]{64}',expected_review) or sha(path)!=expected_review:
        raise ValueError('Explicit bound diagnostic review required')
    review=json.loads(path.read_text('utf-8'))
    if (review.get('reviewer')!='root' or review.get('decision')!='allow_isolated_diagnostic_stage'
            or review.get('scope')!='loopback_diagnostic_only'
            or review.get('local_repair_benefit_demonstrated') is not False
            or review.get('full_allowed') is not False
            or review.get('production_promotion_allowed') is not False
            or review.get('official_calls_allowed')!=0):
        raise ValueError('Isolated diagnostic review has not passed')
    if manifest.get('purpose')!='loopback_diagnostic_only' or manifest.get('full_allowed') is not False:
        raise ValueError('Unreviewed diagnostic scope')
    if review.get('candidate_manifest_sha256')!=expected_manifest:
        raise ValueError('Diagnostic review belongs to another bundle')
    core={p:h for p,h in manifest['files_sha256'].items() if p.startswith('source/heguang_code/')}
    if review.get('candidate_core_sha256')!=core or review.get('capacity_candidate_core_sha256')!=core:
        raise ValueError('Diagnostic review belongs to another candidate')
    if review.get('limited_capacity_gate_passed') is not True:
        raise ValueError('Limited capacity has not passed')
    for field in ('capacity_result_sha256','functional_check_sha256'):
        digest=review.get(field,'')
        if not re.fullmatch('[0-9a-f]{64}',digest) or digest=='0'*64 or digest!=manifest.get(field):
            raise ValueError('Bound diagnostic evidence differs')
    return review



def validate_model(folder,receipt):
    # The model folder is existing trusted configuration. Cache symlinks are
    # allowed, but every asset must have the exact public pinned bytes.
    folder=Path(folder)
    if receipt.get('revision')!='233902d25c440f23af6f7d6e94d2946bac0bee0a':
        raise ValueError('Unreviewed model revision')
    expected={'README.md','onnx/model_quint8_avx2.onnx','tokenizer.json',
              'tokenizer_config.json','special_tokens_map.json','config.json'}
    if len(receipt.get('files',[]))!=6 or {r['path'] for r in receipt['files']}!=expected:
        raise ValueError('Incomplete pinned model inventory')
    for row in receipt['files']:
        p=folder/relative_file(row['path'])
        if not p.is_file() or p.stat().st_size!=row['bytes'] or sha(p)!=row['sha256']:
            raise ValueError('Pinned model asset changed')
