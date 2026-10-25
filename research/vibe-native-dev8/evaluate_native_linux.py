"""Evaluate frozen reviewed Vibe proposals with immutable offline native test images."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
import zlib
from full_suite_protocol import assess, exact_outcomes

KIT = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def assessment(baseline, candidate, declarations):
    if (not baseline['preflight_passed'] or not candidate['preflight_passed']
            or not baseline['events'] or not candidate['events']):
        return {'baseline_admissible': False, 'resolved': False, 'reason': 'Missing native evidence'}
    b, c = baseline['events'], candidate['events']
    try:
        return assess(exact_outcomes(b['collected'], b['events']), exact_outcomes(c['collected'], c['events']),
                      declarations['fail_to_pass'], declarations['pass_to_pass'],
                      baseline_exit=baseline['exit_code'], candidate_exit=candidate['exit_code'],
                      baseline_collection_errors=b['collection_errors'], candidate_collection_errors=c['collection_errors'],
                      guard_reason=baseline['guard_reason'] or candidate['guard_reason'])
    except ValueError as error:
        # Preserve all raw reports even when the protocol rejects the evidence.
        return {'baseline_admissible': False, 'resolved': False,
                'reason': 'Evidence protocol rejected reports: ' + str(error),
                'official_score': False, 'full_allowed': False}


def execute(case, arm, output, image):
    output.mkdir()
    create = ['docker', 'create', '--network=none', '--cap-drop=ALL', '--security-opt=no-new-privileges',
              '--cpus=2', '--memory=6g', '--pids-limit=256', '--mount',
              'type=bind,src=' + str(KIT.resolve()) + ',dst=/kit,readonly', '--workdir', case['working_dir'],
              '--entrypoint', 'python', image, '-B', '/kit/prepare_native_case.py', '--case', case['instance_id'], '--arm', arm]
    cid = subprocess.run(create, check=True, capture_output=True, text=True).stdout.strip()
    if not re.fullmatch('[a-f0-9]{64}', cid):
        raise ValueError('Unexpected container identity')
    start = time.monotonic()
    guard = None
    try:
        with (output / 'execution.log').open('wb') as stream:
            try:
                subprocess.run(['docker', 'start', '-a', cid], stdout=stream, stderr=subprocess.STDOUT, timeout=900)
            except subprocess.TimeoutExpired:
                guard = '900-second execution limit'
    finally:
        state = json.loads(subprocess.run(['docker', 'inspect', cid], check=True, capture_output=True, text=True).stdout)[0]['State']
        exit_code = None if state.get('Running') else state['ExitCode']
        if state.get('OOMKilled'):
            guard = 'Container memory limit'
        recovered = subprocess.run(['docker', 'cp', cid + ':/tmp/qianora-vibe-evidence/.', str(output.resolve())],
                                   capture_output=True, text=True)
        if recovered.returncode:
            guard = guard or 'Container evidence recovery failed'
        subprocess.run(['docker', 'rm', '-f', cid], check=True, capture_output=True)
    events = output / 'run/native-events.json'
    preflight = output / 'preflight.json'
    record = read(events) if events.exists() else None
    if record and record['native_command'] != read(KIT / 'native-test-commands.json')['commands'][case['instance_id']]['argv']:
        raise ValueError('Native test selection changed')
    if record and record['exit_code'] != exit_code:
        guard = guard or 'Container and pytest exit codes differ'
    return {'exit_code': exit_code, 'guard_reason': guard, 'seconds': round(time.monotonic() - start, 3),
            'events': record, 'preflight_passed': preflight.exists(), 'preflight': read(preflight) if preflight.exists() else None,
            'log_sha256': sha(output / 'execution.log'),
            'log_tail': (output / 'execution.log').read_bytes()[-8000:].decode('utf-8', errors='replace')}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--case', required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    for name, expected in read(KIT / 'bundle-hashes.json').items():
        path = (KIT / name).resolve()
        path.relative_to(KIT.resolve())
        if sha(path) != expected:
            raise ValueError('Frozen evaluation bundle changed')
    case = next(c for c in read(KIT / 'manifest.json')['cases'] if c['instance_id'] == a.case)
    subprocess.run(['docker', 'pull', case['image']], check=True, timeout=900)
    info = json.loads(subprocess.run(['docker', 'image', 'inspect', case['image']], check=True, capture_output=True, text=True).stdout)[0]
    if info['Id'] != case['image_id']:
        raise ValueError('Immutable image ID mismatch')
    a.output.mkdir(parents=True, exist_ok=False)
    declarations = read(KIT / a.case / 'declared-tests.json')
    reports = {'baseline': execute(case, 'baseline', a.output / 'baseline', info['Id'])}
    baseline = assessment(reports['baseline'], reports['baseline'], declarations)
    evaluated = {}
    for arm in case['arms']:
        if arm['status'] != 'frozen_reviewed':
            evaluated[arm['arm']] = {'resolved': False, 'proposal_executed': False, 'reason': arm['status']}
        elif not baseline['baseline_admissible']:
            evaluated[arm['arm']] = {'resolved': False, 'proposal_executed': False, 'reason': 'Baseline inadmissible'}
        else:
            reports[arm['arm']] = execute(case, arm['arm'], a.output / arm['arm'], info['Id'])
            evaluated[arm['arm']] = assessment(reports['baseline'], reports[arm['arm']], declarations)
    result = {'case': a.case, 'image_id': info['Id'], 'repo_digests': info.get('RepoDigests', []),
              'reports': reports, 'baseline_assessment': baseline, 'evaluation': evaluated,
              'official_score': False, 'target_solution_read': False, 'model_regenerated': False, 'full_allowed': False}
    path = a.output / 'result.json'
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    compressed = zlib.compress(path.read_bytes(), 9)
    if len(compressed) >= 4 * 1024 * 1024:
        raise ValueError('Evidence exceeds transport limit')
    encoded = base64.b64encode(compressed).decode()
    print('QIANORA_RESULT_SHA256 ' + sha(path))
    for offset in range(0, len(encoded), 12000):
        print('QIANORA_EVIDENCE ' + str(offset // 12000) + ' ' + encoded[offset:offset + 12000])
    print(json.dumps({'case': a.case, 'evaluation': evaluated, 'full_allowed': False}))
    if not baseline['baseline_admissible']:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
