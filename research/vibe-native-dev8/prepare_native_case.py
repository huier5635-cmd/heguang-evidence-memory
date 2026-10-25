"""Python 3.7 container entry: verify frozen originals, apply reviewed patches, record native tests."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def contained(root, name):
    path = (root / name).resolve()
    path.relative_to(root.resolve())
    return path


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--case', required=True)
    parser.add_argument('--arm', required=True)
    args = parser.parse_args()
    kit = Path('/kit')
    case = next(c for c in read(kit / 'manifest.json')['cases'] if c['instance_id'] == args.case)
    repo = Path(case['working_dir']).resolve()
    if not sys.platform.startswith('linux') or Path.cwd().resolve() != repo:
        raise ValueError('Unexpected container working directory')
    permitted = {'baseline'} | {a['arm'] for a in case['arms'] if a['status'] == 'frozen_reviewed'}
    if args.arm not in permitted:
        raise ValueError('Only reviewed valid proposals can execute')
    from normalize_base import restore_declared_generated_files
    assets = {'src/hdmf/_version.py': kit / 'hdmf-export-version.py'} if case['repo'] == 'hdmf-dev/hdmf' else {}
    normalization = restore_declared_generated_files(repo, case['base_commit'],
                                                    case['expected_source_files'], list(assets), assets)
    folder = contained(kit, case['instance_id'])
    # Check the exact text originals used by every valid solver arm, even baseline.
    expected = {}
    for arm in case['arms']:
        if arm['status'] != 'frozen_reviewed':
            continue
        for name, digest in read(folder / arm['arm'] / 'expected-originals.json').items():
            if name in expected and expected[name] != digest:
                raise ValueError('Solver original differs between arms')
            expected[name] = digest
    for name, wanted in expected.items():
        source = contained(repo, name)
        actual = hashlib.sha256(source.read_text(encoding='utf-8').encode()).hexdigest()
        if actual != wanted:
            raise ValueError('Frozen solver original differs: ' + name)
    patches = [folder / 'test.patch']
    if args.arm != 'baseline':
        patches.append(folder / args.arm / 'proposal.patch')
    for patch in patches:
        subprocess.run(['git', 'apply', '--check', str(patch)], cwd=str(repo), check=True)
        subprocess.run(['git', 'apply', str(patch)], cwd=str(repo), check=True)
    evidence = Path('/tmp/qianora-vibe-evidence')
    evidence.mkdir(exist_ok=False)
    receipt = {'base_commit': case['base_commit'], 'arm': args.arm,
               'original_files_verified': len(expected), 'normalization': normalization,
               'python_executable': sys.executable, 'python_version': sys.version,
               'target_solution_read': False, 'model_regenerated': False, 'full_allowed': False}
    (evidence / 'preflight.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    command = [sys.executable, '-B', str(kit / 'record_native_pytest.py'), '--commands',
               str(kit / 'native-test-commands.json'), '--case', args.case,
               '--output', str(evidence / 'run')]
    raise SystemExit(subprocess.run(command, cwd=str(repo), check=False).returncode)


if __name__ == '__main__':
    main()
