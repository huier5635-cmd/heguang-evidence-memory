"""Python 3.7-compatible pre-generation source and original-base test preflight.

No dataset test_patch or solution patch is loaded, installed or inferred.
Tracked tests are restored directly from the fixed BASE git object before use.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

p = argparse.ArgumentParser()
p.add_argument('--case', required=True)
a = p.parse_args()
case = next(c for c in json.loads(Path('/kit/plan.json').read_text())['cases'] if c['case'] == a.case)
repo = Path.cwd().resolve()


def git(*args):
    return subprocess.run(['git', *args], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout


head = git('rev-parse', 'HEAD').decode().strip()
errors, restored, missing = [], [], []
if head != case['base_commit']:
    errors.append('Base HEAD differs')
for name, expected in sorted(case['expected_source_files'].items()):
    path = (repo / name).resolve()
    path.relative_to(repo)
    if not path.is_file():
        missing.append(name)
        continue
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected and head == case['base_commit']:
        body = git('show', head + ':' + name)
        if hashlib.sha256(body).hexdigest() != expected:
            errors.append('Canonical source mismatch: ' + name)
            continue
        path.write_bytes(body)
        restored.append(name)
    elif actual != expected:
        errors.append('Source differs: ' + name)

restored_original_tests = 0
if not errors and not missing:
    # Blindly restore tracked BASE tests; do not read an image's potentially
    # augmented test body. Never import any target test-patch dataset column.
    for prefix in case['original_test_roots']:
        names = git('ls-tree', '-r', '--name-only', head, '--', prefix).decode().splitlines()
        tracked = set(names)
        for name in names:
            path = (repo / name).resolve()
            path.relative_to(repo)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(git('show', head + ':' + name))
            restored_original_tests += 1
        root = repo / prefix
        for path in root.rglob('*.py'):
            name = path.relative_to(repo).as_posix()
            if name not in tracked and '__pycache__' not in path.parts:
                errors.append('Untracked test file; refusing base test execution: ' + name)

pytest_version = None
try:
    import pytest
    pytest_version = pytest.__version__
except Exception as error:
    errors.append('Pytest import: ' + type(error).__name__)
exit_code, timed_out, diagnostics, summary = None, False, '', []
if not errors and not missing:
    with tempfile.TemporaryFile() as log:
        try:
            result = subprocess.run(case['native_argv'], stdout=log, stderr=subprocess.STDOUT, timeout=180)
            exit_code = result.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
        log.seek(0, 2)
        size = log.tell()
        log.seek(0)
        first = log.read(18000).decode('utf-8', errors='replace')
        log.seek(max(0, size - 12000))
        tail = log.read(12000).decode('utf-8', errors='replace')
        diagnostics = first if size <= 18000 else first + '\n[bounded base-test log tail]\n' + tail
        summary = [line for line in tail.splitlines() if re.search(r'\b(passed|failed|error|skipped)\b', line)][-12:]
report = {'case': a.case, 'base_commit': head, 'working_dir': str(repo),
          'source_files_verified': len(case['expected_source_files']) - len(missing),
          'restored_source_files': restored, 'missing_source_files': missing, 'errors': errors,
          'restored_original_base_test_files': restored_original_tests,
          'python_version': sys.version, 'python_executable': sys.executable, 'pytest_version': pytest_version,
          'base_native_argv': case['native_argv'], 'base_test_exit': exit_code,
          'base_test_timed_out': timed_out, 'base_test_summary': summary,
          'metadata_passed': not errors and not missing,
          'original_base_tests_passed': exit_code == 0 and not timed_out,
          'dataset_test_patch_loaded': False, 'target_solution_loaded': False,
          'generated_code_executed': False, 'generation_started': False, 'full_allowed': False}
print('QIANORA_DEV17_PREFLIGHT ' + json.dumps(report, sort_keys=True))
print('QIANORA_BASE_TEST_DIAGNOSTICS_BEGIN')
print(diagnostics)
print('QIANORA_BASE_TEST_DIAGNOSTICS_END')
if not report['metadata_passed'] or not report['original_base_tests_passed']:
    raise SystemExit(2)
