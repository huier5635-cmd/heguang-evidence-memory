"""Fixed public wheels for one before-generation local compatibility environment.

Hosts download verified data only. Installation is allowed solely inside the
offline /kit Linux container, under /tmp; project/test files are never changed.
"""
import hashlib
import importlib
import importlib.metadata
import json
from pathlib import Path, PurePosixPath
import subprocess
import sys
import urllib.parse
import urllib.request

KIT=Path(__file__).resolve().parent
WOODWORK='alteryx__woodwork-640'
MANIFEST_SHA256='0ab4d21b3cdbd6dfac38cdffd43de948b591a8b22f259f84a0b28c3f10414c37'
TARGET=Path('/tmp/qianora-pinned-python')


def sha(path):
    with Path(path).open('rb') as stream:
        value=hashlib.sha256()
        for chunk in iter(lambda:stream.read(1024*1024),b''):value.update(chunk)
        return value.hexdigest()


def manifest():
    path=KIT/'dependency-assets.json'
    if sha(path)!=MANIFEST_SHA256:raise ValueError('Fixed dependency manifest changed')
    result=json.loads(path.read_text('utf-8'));items=result['packages']
    if len(items)!=2 or {(p['package'],p['version']) for p in items}!={('pandas','1.5.3'),('numpy','1.26.4')}:
        raise ValueError('Unexpected native dependency set')
    if sum(p['bytes'] for p in items)>40*1024**2:raise ValueError('Dependency transfer budget exceeded')
    for item in items:
        local=PurePosixPath(item['local_path']);url=urllib.parse.urlparse(item['url'])
        if (local.parts!=('wheels',item['filename']) or '/' in item['filename'] or '\\' in item['filename']
                or ':' in item['filename'] or not item['filename'].endswith('.whl')
                or 'cp310-cp310-manylinux' not in item['filename'] or 'x86_64' not in item['filename']
                or url.scheme!='https' or url.netloc!='files.pythonhosted.org'
                or not url.path.endswith('/'+item['filename']) or not 0<item['bytes']<24*1024**2
                or len(item['sha256'])!=64 or any(c not in '0123456789abcdef' for c in item['sha256'])):
            raise ValueError('Unsafe native wheel descriptor')
    return result


def verify_assets():
    data=manifest()
    for item in data['packages']:
        path=asset_path(item)
        if path.is_symlink() or path.stat().st_size!=item['bytes'] or sha(path)!=item['sha256']:
            raise ValueError('Fixed native wheel bytes differ')
    return data


def asset_path(item):
    path=KIT/item['local_path']
    path.resolve().relative_to(KIT.resolve())
    if path.parent.is_symlink() or path.is_symlink():raise ValueError('Native asset path is a symlink')
    return path


def download_for_case(identity):
    if identity!=WOODWORK:return None
    data=manifest()
    for item in data['packages']:
        path=asset_path(item)
        if path.exists():
            if path.is_symlink() or path.stat().st_size!=item['bytes'] or sha(path)!=item['sha256']:
                raise ValueError('Preserve mismatching cached native asset; do not overwrite')
            continue
        path.parent.mkdir(parents=True,exist_ok=True)
        with urllib.request.urlopen(item['url'],timeout=45) as response:
            body=response.read(item['bytes']+1)
        if len(body)!=item['bytes'] or hashlib.sha256(body).hexdigest()!=item['sha256']:
            raise ValueError('Native asset transfer size/hash mismatch')
        with path.open('xb') as stream:stream.write(body)
    verify_assets()
    return {'download_only':True,'manifest_sha256':MANIFEST_SHA256,'packages':[p['filename'] for p in data['packages']]}


def prepare_inside(identity):
    if identity!=WOODWORK:return None
    if (not sys.platform.startswith('linux') or KIT!=Path('/kit') or sys.version_info[:2]!=(3,10)):
        raise ValueError('Native wheels install only in the fixed /kit Linux Python3.10 container')
    if TARGET.exists():raise ValueError('New ephemeral dependency target required')
    data=verify_assets()
    before={p['package']:importlib.metadata.version(p['package']) for p in data['packages']}
    command=[sys.executable,'-m','pip','install','--disable-pip-version-check','--no-index','--no-deps',
        '--only-binary=:all:','--no-compile','--no-cache-dir','--target',str(TARGET),
        *[str(KIT/p['local_path']) for p in data['packages']]]
    done=subprocess.run(command,check=False,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=120)
    if done.returncode:raise RuntimeError('Offline fixed dependency install failed: '+done.stdout[-1800:].decode('utf-8',errors='replace'))
    sys.path.insert(0,str(TARGET));installed={};paths={}
    for item in data['packages']:
        module=importlib.import_module(item['package']);installed[item['package']]=module.__version__;paths[item['package']]=str(Path(module.__file__).resolve())
        if module.__version__!=item['version'] or TARGET not in Path(module.__file__).resolve().parents:
            raise ValueError('Loaded native dependency differs from fixed overlay')
    return {'manifest_sha256':MANIFEST_SHA256,'before_versions':before,'installed_versions':installed,
        'loaded_module_paths':paths,'offline_install':True,'target':str(TARGET),
        'pip_output_sha256':hashlib.sha256(done.stdout).hexdigest(),'project_or_test_code_changed':False}
