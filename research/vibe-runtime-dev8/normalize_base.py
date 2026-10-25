"""Restore only declared generated files to independently pinned base bytes."""
import hashlib
import subprocess
from pathlib import Path


def restore_declared_generated_files(repo, commit, expected, allowed):
    repo = Path(repo).resolve()
    head = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'],
                          check=True, stdout=subprocess.PIPE).stdout.decode().strip()
    if head != commit:
        raise ValueError('Wrong base commit')
    pending = []
    for name, wanted in expected.items():
        path = (repo / name).resolve()
        path.relative_to(repo)
        if not path.is_file():
            raise ValueError('Missing base source: ' + name)
        original = hashlib.sha256(path.read_bytes()).hexdigest()
        if original == wanted:
            continue
        if name not in allowed:
            raise ValueError('Undeclared source difference: ' + name)
        fixed = subprocess.run(['git', '-C', str(repo), 'show', commit + ':' + name],
                               check=True, stdout=subprocess.PIPE).stdout
        if hashlib.sha256(fixed).hexdigest() != wanted:
            raise ValueError('Git base bytes differ from independent manifest: ' + name)
        pending.append((name, path, original, fixed, wanted))
    # All paths and originals are checked before any file is changed.
    receipt = []
    for name, path, original, fixed, wanted in pending:
        path.write_bytes(fixed)
        if hashlib.sha256(path.read_bytes()).hexdigest() != wanted:
            raise ValueError('Restored source checksum mismatch')
        receipt.append({'path': name, 'image_sha256': original, 'restored_sha256': wanted})
    return receipt
