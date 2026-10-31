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
import time
import urllib.request

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
    from base_test_sources import restore_original_tests
    restored_original_tests = restore_original_tests(repo, case)

pytest_version = None
try:
    import pytest
    pytest_version = pytest.__version__
except Exception as error:
    errors.append('Pytest import: ' + type(error).__name__)
dependency_ready = None
if case.get('elasticsearch_dependency') and not errors and not missing:
    dependency_ready = False
    for attempt in range(60):
        try:
            with urllib.request.urlopen('http://127.0.0.1:9200', timeout=2) as response:
                info = json.load(response)
            if info['version']['number'] != case['elasticsearch_dependency']['version']:
                errors.append('Elasticsearch version differs')
                break
            dependency_ready = True
            break
        except (OSError, ValueError, KeyError):
            time.sleep(1)
    if not dependency_ready:
        errors.append('Fixed isolated Elasticsearch dependency not ready')
exit_code, timed_out, diagnostics, summary = None, False, '', []
native_events, raw_events = None, None
if not errors and not missing:
    from original_base_evidence import capture_native_events
    with tempfile.TemporaryDirectory(prefix='qianora-original-base-') as temporary:
        output = Path(temporary)/'capture'
        captured = capture_native_events('/kit/native-test-commands.json', a.case,
            '/kit/record_native_pytest.py', repo, output, timeout=180)
        exit_code, timed_out = captured['exit_code'], captured['timed_out']
        diagnostics, native_events = captured['diagnostics'], captured['record']
        if captured['full_events_retained']:
            raw_events = (output/'run/native-events.json').read_bytes()
            if hashlib.sha256(raw_events).hexdigest() != captured['record_sha256']:
                raise ValueError('Original BASE event bytes changed')
        summary = [line for line in diagnostics.splitlines() if re.search(r'\b(passed|failed|error|skipped)\b', line)][-12:]
report = {'case': a.case, 'base_commit': head, 'working_dir': str(repo),
          'source_files_verified': len(case['expected_source_files']) - len(missing),
          'restored_source_files': restored, 'missing_source_files': missing, 'errors': errors,
          'restored_original_base_test_files': restored_original_tests,
          'python_version': sys.version, 'python_executable': sys.executable, 'pytest_version': pytest_version,
          'base_native_argv': case['native_argv'], 'base_test_exit': exit_code,
          'base_test_timed_out': timed_out, 'base_test_summary': summary,
          'metadata_passed': not errors and not missing,
          'original_base_tests_passed': exit_code == 0 and not timed_out and raw_events is not None,
          'original_base_events_sha256': hashlib.sha256(raw_events).hexdigest() if raw_events is not None else None,
          'original_base_events_bytes': len(raw_events) if raw_events is not None else None,
          'full_original_base_events_captured_before_generation': raw_events is not None,
          'isolated_elasticsearch_ready': dependency_ready,
          'dataset_test_patch_loaded': False, 'target_solution_loaded': False,
          'generated_code_executed': False, 'generation_started': False, 'full_allowed': False}
print('QIANORA_DEV26_PREFLIGHT ' + json.dumps(report, sort_keys=True))
print('QIANORA_BASE_TEST_DIAGNOSTICS_BEGIN')
print(diagnostics)
print('QIANORA_BASE_TEST_DIAGNOSTICS_END')
if raw_events is not None:
    import base64, zlib
    encoded = base64.b64encode(zlib.compress(raw_events, 9)).decode('ascii')
    print('QIANORA_ORIGINAL_BASE_CASE ' + a.case)
    print('QIANORA_ORIGINAL_BASE_SHA256 ' + hashlib.sha256(raw_events).hexdigest())
    for offset in range(0, len(encoded), 12000):
        print('QIANORA_ORIGINAL_BASE_EVENTS ' + str(offset//12000) + ' ' + encoded[offset:offset+12000])
if not report['metadata_passed'] or not report['original_base_tests_passed']:
    raise SystemExit(2)
