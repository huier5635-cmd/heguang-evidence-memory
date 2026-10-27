"""Restore declared original BASE tests before applying any target test patch."""
from pathlib import Path, PurePosixPath
import subprocess


def restore_original_tests(repo, case):
    repo = Path(repo).resolve()
    head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=str(repo), check=True,
                          stdout=subprocess.PIPE).stdout.decode().strip()
    if head != case['base_commit']:
        raise ValueError('Original-test base HEAD differs')
    restored = 0
    for prefix in case['original_test_roots']:
        parts = prefix.split('/')
        if (PurePosixPath(prefix).is_absolute() or any(p in {'', '.', '..', '.git', '.env'} for p in parts)
                or '\\' in prefix or ':' in prefix or not {'test', 'tests'}.intersection(parts)):
            raise ValueError('Unsafe original-test root')
        raw = subprocess.run(['git', 'ls-tree', '-rz', head, '--', prefix], cwd=str(repo), check=True,
                             stdout=subprocess.PIPE).stdout
        tracked = set()
        for entry in raw.split(b'\0'):
            if not entry:
                continue
            info, name = entry.split(b'\t', 1)
            mode, kind, object_id = info.decode().split()
            name = name.decode('utf-8')
            if mode not in {'100644', '100755'} or kind != 'blob':
                raise ValueError('Nonregular original-test object')
            relative = PurePosixPath(name)
            if relative.is_absolute() or '..' in relative.parts or not name.startswith(prefix + '/'):
                raise ValueError('Original test escaped declared root')
            destination = repo.joinpath(*relative.parts)
            if any(p.is_symlink() for p in [destination, *destination.parents] if p != repo.parent):
                raise ValueError('Symlink in original-test destination')
            destination.resolve().relative_to(repo)
            body = subprocess.run(['git', 'cat-file', 'blob', object_id], cwd=str(repo), check=True,
                                  stdout=subprocess.PIPE).stdout
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(body)
            tracked.add(name)
            restored += 1
        for path in (repo / prefix).rglob('*.py'):
            if path.relative_to(repo).as_posix() not in tracked and '__pycache__' not in path.parts:
                raise ValueError('Untracked Python test before target patch')
    return restored
