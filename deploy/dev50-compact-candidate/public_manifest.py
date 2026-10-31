"""Bind an authorized diagnostic Smoke candidate; never assert Full readiness."""
import hashlib
import json
from pathlib import Path
import re
import sys

PARENT_MANIFEST_SHA='b6a62a0e9689d071b016bd2cc40818271fae00bb9d6ea2b0a8c4cb2eb4160add'
PARENT=Path('/opt/heguang-coding/staging/dev45ctx49-'+PARENT_MANIFEST_SHA[:12])

def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def validate(folder,expected):
    folder=Path(folder)
    if not re.fullmatch('[0-9a-f]{64}',expected) or sha(folder/'public-manifest.json')!=expected:
        raise ValueError('Public context manifest differs')
    manifest=json.loads((folder/'public-manifest.json').read_text('utf-8'))
    if (manifest.get('version')!='0.2.0.dev45+ctx6x16k' or manifest.get('scope')!='official_smoke_diagnostic_only'
            or manifest.get('full_allowed') is not False or manifest.get('parent_manifest_sha256')!=PARENT_MANIFEST_SHA
            or manifest.get('actual_cloud_profile_preflight_passed') is not True
            or manifest.get('full_parallel_history_capacity_passed') is not True
            or manifest.get('model_input_context_budget')!={'records':6,'characters':16000}
            or manifest.get('candidate_core_modules')!=21):
        raise ValueError('Actual cloud profile diagnostic review has not passed')
    for name in ['cloud_profile_receipt_sha256','public_entry_check_sha256',
                 'parent_review_sha256','whole_record_budget_source_sha256']:
        value=manifest.get(name,'')
        if not re.fullmatch('[0-9a-f]{64}',value) or value=='0'*64:
            raise ValueError('Missing actual bound evidence')
    # Diagnostic authorization does not turn unavailable/negative native evidence into a win.
    gain=manifest.get('repair_benefit_demonstrated')
    if type(gain) is not bool:
        raise ValueError('Native repair evidence status must be explicit')
    native=manifest.get('native_repair_receipt_sha256')
    if gain and (not isinstance(native,str) or not re.fullmatch('[0-9a-f]{64}',native) or native=='0'*64):
        raise ValueError('A repair gain claim requires its actual bound audit')
    if not gain and native is not None and (not isinstance(native,str) or not re.fullmatch('[0-9a-f]{64}',native) or native=='0'*64):
        raise ValueError('Invalid native evidence reference')
    expected_files={'public_manifest.py','public_runner.py','public_unit.py','routing.py'}
    if set(manifest.get('files_sha256',{}))!=expected_files:
        raise ValueError('Unexpected public helper inventory')
    for name,digest in manifest['files_sha256'].items():
        path=folder/name
        if path.is_symlink() or not path.is_file() or sha(path)!=digest:
            raise ValueError('Public context helper changed')
    return manifest

def parent_runtime():
    sys.path.insert(0,str(PARENT))
    from manifest_validation import validate_bundle
    return validate_bundle(PARENT,PARENT_MANIFEST_SHA)
