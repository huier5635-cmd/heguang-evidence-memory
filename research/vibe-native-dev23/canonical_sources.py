"""Python 3.7 verification and explicit canonical-version normalization inside fixed images."""
import hashlib
from pathlib import Path
import subprocess


def head(path):
    return subprocess.run(['git', '-C', str(path), 'rev-parse', 'HEAD'],
                          check=True, stdout=subprocess.PIPE).stdout.decode().strip()


def verify_and_normalize(repo, case, kit):
    repo, kit = Path(repo).resolve(), Path(kit).resolve()
    if head(repo) != case['base_commit']:
        raise ValueError('Repository HEAD differs from frozen base')
    pending = []
    for name, wanted in case['expected_source_files'].items():
        path = (repo / name).resolve(); path.relative_to(repo)
        if not path.is_file(): raise ValueError('Missing original source: ' + name)
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual == wanted: continue
        if name not in case.get('canonical_sources', {}):
            raise ValueError('Unapproved source difference: ' + name)
        asset = (kit / case['canonical_sources'][name]).resolve(); asset.relative_to(kit)
        body = asset.read_bytes()
        if hashlib.sha256(body).hexdigest() != wanted:
            raise ValueError('Canonical source asset checksum mismatch')
        pending.append((name, path, actual, body, wanted))
    dependencies = []
    for dependency in case.get('dependencies', []):
        path = (repo / dependency['path']).resolve(); path.relative_to(repo)
        actual = head(path)
        if actual != dependency['commit']:
            raise ValueError('Read-only gitlink revision mismatch')
        dependencies.append({'path': dependency['path'], 'commit': actual})
    normalization = []
    for name, path, actual, body, wanted in pending:
        path.write_bytes(body)
        if hashlib.sha256(path.read_bytes()).hexdigest() != wanted:
            raise ValueError('Canonical source restoration failed')
        normalization.append({'path': name, 'image_sha256': actual, 'restored_sha256': wanted})
    return normalization, dependencies
