"""Bind the public diagnostic entry to the exact tested private runtime."""
import hashlib
import json
from pathlib import Path
import re
import sys

PARENT_MANIFEST_SHA='ccfbdab44d05222f8a04c89b7851894b4abc88a634837e31680829c740fbaa08'
PARENT=Path('/opt/heguang-coding/staging/dev25diag-'+PARENT_MANIFEST_SHA[:12])

def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def validate(folder,expected):
    folder=Path(folder)
    if not re.fullmatch('[0-9a-f]{64}',expected) or sha(folder/'public-manifest.json')!=expected:
        raise ValueError('Public diagnostic manifest differs')
    manifest=json.loads((folder/'public-manifest.json').read_text('utf-8'))
    if (manifest.get('scope')!='official_smoke_diagnostic_only' or manifest.get('full_allowed') is not False
            or manifest.get('repair_benefit_demonstrated') is not False
            or manifest.get('parent_manifest_sha256')!=PARENT_MANIFEST_SHA
            or manifest.get('actual_cloud_diagnostic_preflight_passed') is not True
            or manifest.get('diagnostic_preflight_scope')!='single_81mb_auth_conflict_source_audit_restart_only'
            or manifest.get('full_parallel_history_capacity_passed') is not False):
        raise ValueError('Diagnostic publication review has not passed')
    if not re.fullmatch('[0-9a-f]{64}',manifest.get('actual_cloud_diagnostic_receipt_sha256','')):
        raise ValueError('Actual diagnostic preflight evidence required')
    expected_files={'public_manifest.py','public_runner.py','public_unit.py','routing.py'}
    if set(manifest.get('files_sha256',{}))!=expected_files:
        raise ValueError('Public helper inventory differs')
    for name,digest in manifest['files_sha256'].items():
        path=folder/name
        if path.is_symlink() or not path.is_file() or sha(path)!=digest:
            raise ValueError('Public diagnostic helper changed')
    return manifest

def parent_runtime():
    sys.path.insert(0,str(PARENT))
    from manifest_validation import validate_bundle
    return validate_bundle(PARENT,PARENT_MANIFEST_SHA)
