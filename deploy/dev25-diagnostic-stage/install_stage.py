"""Create or stop one manifest-bound loopback stage; no production promotion."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

from manifest_validation import sha,validate_bundle,validate_review,regular_bundle_file
from stage_unit import locations,render


ENV_FILE=Path('/etc/heguang-coding.env')
UNIT_ROOT=Path('/etc/systemd/system')


def health(port):
    try:
        with urllib.request.urlopen('http://127.0.0.1:'+str(port)+'/health',timeout=3) as response:
            data=json.loads(response.read(4097))
            return {'http_status':response.status,**{k:data.get(k) for k in ('status','version','track')}}
    except (OSError,ValueError) as error:
        return {'error_type':type(error).__name__}


def checked_paths(raw):
    p=locations(raw);identity=hashlib.sha256(raw).hexdigest()[:12]
    if p!={'identity':identity,'release':'/opt/heguang-coding/staging/dev25diag-'+identity,
            'data':'/var/lib/heguang-coding-stage-dev25diag-'+identity,
            'unit':'heguang-coding-stage-dev25diag-'+identity+'.service'}:
        raise ValueError('Unreviewed stage locations')
    return p


def no_symlink_ancestors(path):
    path=Path(path)
    if any(p.is_symlink() for p in (path,*path.parents)):
        raise ValueError('Symlink in stage destination')


def require_linux_root():
    if sys.platform!='linux' or os.geteuid()!=0:
        raise PermissionError('Staging mutations require Linux root')


def write_new(path,body,mode):
    path=Path(path)
    stream=path.open('xb')
    created=os.fstat(stream.fileno())
    try:
        with stream:
            stream.write(body)
            stream.flush()
            os.chmod(path,mode)
    except BaseException:
        current=path.stat(follow_symlinks=False)
        if (current.st_dev,current.st_ino)==(created.st_dev,created.st_ino):path.unlink()
        raise


def command(argv,log=None,timeout=60):
    # No inherited model/cloud credentials or pip configuration. No shell.
    env={'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
         'LANG':'C.UTF-8','PIP_CONFIG_FILE':'/dev/null','PIP_NO_INPUT':'1',
         'PIP_DISABLE_PIP_VERSION_CHECK':'1','PYTHONDONTWRITEBYTECODE':'1'}
    subprocess.run(argv,env=env,stdin=subprocess.DEVNULL,stdout=log or subprocess.DEVNULL,
                   stderr=subprocess.STDOUT,check=True,timeout=timeout)


def owner_check(paths,manifest_digest,review_digest):
    release=Path(paths['release']);data=Path(paths['data']);unit=UNIT_ROOT/paths['unit']
    for p in (release,data,unit):no_symlink_ancestors(p)
    owner=json.loads(regular_bundle_file(release,'installation-owner.json').read_text('utf-8'))
    raw=regular_bundle_file(release,'runtime-manifest.json').read_bytes()
    if checked_paths(raw)!=paths:
        raise ValueError('Stage paths differ from owned manifest')
    if (owner.get('manifest_sha256')!=manifest_digest or owner.get('review_sha256')!=review_digest
            or sha(release/'runtime-manifest.json')!=manifest_digest
            or owner.get('unit_sha256')!=hashlib.sha256(render((release/'runtime-manifest.json').read_bytes()).encode()).hexdigest()):
        raise ValueError('Stage ownership changed')
    if unit.exists() and sha(unit)!=owner['unit_sha256']:
        raise ValueError('Stage unit was changed outside this installation')
    return owner


def stop_owned(paths,manifest_digest,review_digest):
    owner_check(paths,manifest_digest,review_digest)
    unit=UNIT_ROOT/paths['unit']
    if unit.exists():
        command(['systemctl','stop',paths['unit']],timeout=30)
        unit.unlink()
        command(['systemctl','daemon-reload'])
    # Deliberately retain the exact release, test database and protected log.
    return {'stopped':True,'release_and_data_retained':True,'public_route_changed':False,'full_allowed':False}


def install(bundle,manifest_digest,review,review_digest):
    require_linux_root()
    previous=os.umask(0o022)
    try:return _install(bundle,manifest_digest,review,review_digest)
    finally:os.umask(previous)


def _install(bundle,manifest_digest,review,review_digest):
    require_linux_root()
    raw,manifest=validate_bundle(bundle,manifest_digest)
    validate_review(review,review_digest,manifest,manifest_digest)
    paths=checked_paths(raw);release=Path(paths['release']);data=Path(paths['data']);unit=UNIT_ROOT/paths['unit']
    for p in (release,data,unit):
        no_symlink_ancestors(p)
        if p.exists():raise FileExistsError('Stage destination already exists; preserve it')
    if not ENV_FILE.is_file() or ENV_FILE.is_symlink() or ENV_FILE.stat().st_mode&0o777!=0o600:
        raise ValueError('Existing protected environment is not ready')
    env_before=sha(ENV_FILE)
    before={str(p):health(p) for p in (8751,8752)}
    if before['8752'].get('http_status')!=200 or before['8752'].get('version')!='0.2.0.dev6+obs1':
        raise ValueError('Production coding version differs from the reviewed starting point')
    if shutil.disk_usage('/var/lib').free<2*1024**3:
        raise RuntimeError('Staging disk reserve below2GiB')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',8753))
    import pwd
    account=pwd.getpwnam('admin')
    release.parent.mkdir(parents=True,exist_ok=True)
    release.mkdir(mode=0o755);data.mkdir(mode=0o700);os.chown(data,account.pw_uid,account.pw_gid)
    unit_body=render(raw).encode()
    owner={'manifest_sha256':manifest_digest,'review_sha256':review_digest,
           'unit_sha256':hashlib.sha256(unit_body).hexdigest(),'production_before':before,
           'public_route_changed':False,'full_allowed':False}
    write_new(release/'installation-owner.json',(json.dumps(owner,indent=2)+'\n').encode(),0o644)
    write_new(release/'runtime-manifest.json',raw,0o644)
    unit_created=False
    try:
        for name,digest in manifest['files_sha256'].items():
            destination=release/name;destination.parent.mkdir(parents=True,exist_ok=True,mode=0o755)
            body=(Path(bundle)/name).read_bytes()
            if hashlib.sha256(body).hexdigest()!=digest:raise ValueError('Bundle changed during copy')
            write_new(destination,body,0o644)
        log_path=data/'installation.log'
        with log_path.open('xb') as log:
            os.chmod(log_path,0o600)
            command(['/usr/bin/python3','-m','venv',str(release/'venv')],log,120)
            command([str(release/'venv/bin/python'),'-m','pip','install','--only-binary=:all:',
                     '--no-cache-dir','--requirement',str(release/'requirements.txt')],log,300)
        write_new(unit,unit_body,0o644);unit_created=True
        command(['systemctl','daemon-reload'])
        command(['systemctl','start',paths['unit']],timeout=30)
        stage=None
        for _ in range(30):
            stage=health(8753)
            if stage.get('http_status')==200 and stage.get('version')=='0.2.0.dev25':break
            time.sleep(1)
        else:raise RuntimeError('Bounded stage readiness failed')
        after={str(p):health(p) for p in (8751,8752)}
        if before!=after or sha(ENV_FILE)!=env_before:
            raise RuntimeError('Original service or protected configuration changed')
        return {'complete':True,'stage_health':stage,'unit':paths['unit'],
            'original_health_unchanged':True,'protected_environment_unchanged':True,
            'actual_probe_and_resource_validation_still_required':True,
            'public_route_changed':False,'full_allowed':False}
    except BaseException:
        if unit_created:stop_owned(paths,manifest_digest,review_digest)
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['plan','install','stop'])
    parser.add_argument('--bundle',type=Path,default=Path(__file__).resolve().parent)
    parser.add_argument('--manifest-sha256',required=True)
    parser.add_argument('--review',type=Path)
    parser.add_argument('--review-sha256')
    args=parser.parse_args()
    raw,_=validate_bundle(args.bundle,args.manifest_sha256);paths=checked_paths(raw)
    if args.action=='plan':
        result={'paths':paths,'loopback_port':8753,'runtime_seconds':900,
                'source_modified':False,'server_mutation':False,'full_allowed':False}
    else:
        require_linux_root()
        if not args.review_sha256:raise ValueError('Bound review digest required')
        if args.action=='install':
            if args.review is None:raise ValueError('Actual promotion review required')
            result=install(args.bundle,args.manifest_sha256,args.review,args.review_sha256)
        else:result=stop_owned(paths,args.manifest_sha256,args.review_sha256)
    print(json.dumps(result,sort_keys=True))


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(json.dumps({'complete':False,'failure_type':type(error).__name__,'full_allowed':False}),flush=True)
        raise SystemExit(2)
