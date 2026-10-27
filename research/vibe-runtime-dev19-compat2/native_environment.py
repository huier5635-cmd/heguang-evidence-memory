"""Validate the predeclared native environment without shell evaluation."""
import hashlib
from pathlib import Path

WOODWORK = 'alteryx__woodwork-640'
COMPAT_SHA256 = '36fd5b9df22a9f8a7e1f05e7609aae9d899ec1fbd33046c7573fdf5612ae0c0f'
COMPAT_PATH = Path(__file__).resolve().parent / 'compat/sitecustomize.py'


def checked_environment(case, contract):
    expected=contract['commands'][case['instance_id']].get('environment',{})
    actual=case.get('test_environment',{})
    if not isinstance(actual,dict) or actual!=expected:
        raise ValueError('Native test environment differs from frozen contract')
    if actual:
        declared=contract['commands'][case['instance_id']]
        if (case['instance_id'] != WOODWORK or actual != {'PYTHONPATH':'/tmp/qianora-pinned-python:/kit/compat'}
                or declared.get('compatibility_file') != 'compat/sitecustomize.py'
                or declared.get('compatibility_sha256') != COMPAT_SHA256
                or declared.get('dependency_manifest_sha256') != '0ab4d21b3cdbd6dfac38cdffd43de948b591a8b22f259f84a0b28c3f10414c37'):
            raise ValueError('Unsupported native environment override')
        if COMPAT_PATH.is_symlink() or hashlib.sha256(COMPAT_PATH.read_bytes()).hexdigest()!=COMPAT_SHA256:
            raise ValueError('Fixed legacy alias bytes changed')
    if actual:
        manifest=Path(__file__).resolve().parent/'dependency-assets.json'
        if hashlib.sha256(manifest.read_bytes()).hexdigest()!='0ab4d21b3cdbd6dfac38cdffd43de948b591a8b22f259f84a0b28c3f10414c37':
            raise ValueError('Fixed dependency manifest changed')
    return actual
