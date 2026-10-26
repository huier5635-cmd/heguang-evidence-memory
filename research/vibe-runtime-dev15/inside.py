"""Python 3.7 metadata-only inspection of fixed source and compiler availability."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

p = argparse.ArgumentParser(); p.add_argument('--case', required=True); a = p.parse_args()
kit = Path('/kit'); plan = json.loads((kit / 'plan.json').read_text(encoding='utf-8'))
case = next(c for c in plan['cases'] if c['case'] == a.case)
repo = Path.cwd().resolve()
head = subprocess.run(['git', 'rev-parse', 'HEAD'], check=True, stdout=subprocess.PIPE).stdout.decode().strip()
errors, normalized, missing, changed, dependency_heads = [], [], [], [], []
if head != case['base_commit']: errors.append('Repository HEAD mismatch')
for name, expected in case['expected_source_files'].items():
    path = (repo / name).resolve(); path.relative_to(repo)
    if not path.is_file():
        missing.append(name); continue
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected and name in case['canonical_sources'] and head == case['base_commit']:
        asset = (kit / case['canonical_sources'][name]).resolve(); asset.relative_to(kit.resolve())
        body = asset.read_bytes()
        if hashlib.sha256(body).hexdigest() != expected:
            errors.append('Canonical source checksum mismatch: ' + name)
        else:
            path.write_bytes(body)
            normalized.append({'path': name, 'image_sha256': actual, 'restored_sha256': expected})
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected: changed.append({'path': name, 'image_sha256': actual, 'expected_sha256': expected})
for dependency in case['dependencies']:
    path = (repo / dependency['path']).resolve(); path.relative_to(repo)
    result = subprocess.run(['git', '-C', str(path), 'rev-parse', 'HEAD'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    actual = result.stdout.decode(errors='replace').strip()
    dependency_heads.append({'path': dependency['path'], 'expected': dependency['commit'], 'actual': actual})
    if result.returncode or actual != dependency['commit']: errors.append('Gitlink HEAD mismatch: ' + dependency['path'])
pytest_version = None
try:
    import pytest
    pytest_version = pytest.__version__
except Exception as error: errors.append('Pytest import: ' + type(error).__name__)
compiler_paths = {name: shutil.which(name) for name in case['required_compilers']}
for name, value in compiler_paths.items():
    if value is None: errors.append('Required compiler missing: ' + name)
report = {'case': case['case'], 'working_dir': str(repo), 'expected_base_commit': case['base_commit'], 'actual_base_commit': head,
          'source_files_expected': len(case['expected_source_files']), 'missing_source_files': missing,
          'changed_source_files': changed, 'normalization': normalized, 'dependency_heads': dependency_heads,
          'python_executable': sys.executable, 'python_version': sys.version, 'pytest_version': pytest_version,
          'compiler_paths': compiler_paths, 'errors': errors,
          'target_tests_or_solutions_loaded': False, 'tests_executed': False, 'generated_code_executed': False, 'full_allowed': False}
print('QIANORA_VIBE_PREFLIGHT ' + json.dumps(report, sort_keys=True))
if errors or missing or changed: raise SystemExit(2)
