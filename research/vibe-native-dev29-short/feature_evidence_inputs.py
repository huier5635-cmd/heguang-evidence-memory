"""Prove added local imports against unchanged BASE modules, including src layouts."""
import hashlib
from pathlib import Path, PurePosixPath

from feature_import_protocol import imports, safe_path


def contained(repo, name):
    path = (Path(repo)/safe_path(name)).resolve()
    path.relative_to(Path(repo).resolve())
    return path


def snapshot_tests(repo, paths):
    if len(paths)>100 or len(paths)!=len(set(paths)):
        raise ValueError('Explicit bounded test file set required')
    result, total = {}, 0
    for name in paths:
        path=contained(repo,name)
        raw=path.read_bytes() if path.exists() else b''
        total+=len(raw)
        if len(raw)>2*1024*1024 or total>4*1024*1024:
            raise ValueError('Complete test text exceeds evidence bound')
        result[name]=raw.decode('utf-8')
    return result


def import_inputs(repo, original_tests, patched_tests, production_prefixes, expected_sources):
    prefixes=[]
    for name in production_prefixes:
        path=PurePosixPath(name)
        if (path.is_absolute() or '\\' in name or ':' in name
                or any(p in {'','.','..','.git','.env'} for p in name.split('/'))
                or not path.name.isidentifier()):
            raise ValueError('Explicit production package directories required')
        prefixes.append((path.name,path.parent))
    if not prefixes:
        raise ValueError('Missing production package roots')
    packages=sorted({name for name,_ in prefixes})
    modules=set()
    for name,after in patched_tests.items():
        modules.update(module for module,_ in imports(after,name)-imports(original_tests.get(name,''),name)
                       if module.split('.')[0] in packages)
    sources,lineage,total={},{},0
    for module in sorted(modules):
        logical=module.replace('.','/')
        possible=[]
        for package,parent in prefixes:
            if module.split('.')[0] != package:
                continue
            for suffix in ('.py','/__init__.py'):
                actual=(parent/(logical+suffix)).as_posix()
                if actual in expected_sources:
                    possible.append((logical+suffix,actual))
        possible=list(dict.fromkeys(possible))
        if len(possible)!=1:
            continue
        logical,actual=possible[0]
        raw=contained(repo,actual).read_bytes()
        digest=hashlib.sha256(raw).hexdigest()
        if digest!=expected_sources[actual]:
            raise ValueError('Original module differs from frozen BASE source')
        total+=len(raw)
        if len(raw)>2*1024*1024 or total>4*1024*1024:
            raise ValueError('Complete local-module proof exceeds evidence bound')
        sources[logical]=raw.decode('utf-8')
        lineage[logical]={'actual_path':actual,'sha256':digest}
    return {'arguments':{'original_sources':sources,'original_tests':original_tests,
                         'patched_tests':patched_tests,'production_packages':packages},
            'source_lineage':lineage,'captured_before_proposal_application':True}
