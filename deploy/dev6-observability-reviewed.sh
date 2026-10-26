#!/bin/bash
set -eu
python3 - <<'QIANORA_OBSERVABILITY_UPGRADE'
import hashlib,json,os,pathlib,re,subprocess,time,urllib.request,urllib.error,uuid
from datetime import datetime,timezone

base=pathlib.Path('/opt/heguang-coding');app=base/'app'
envfile=pathlib.Path('/etc/heguang-coding.env');unit='heguang-coding.service'
commit='1968029da61dd29891d95f6cc9330748f6bfaf62'
manifest_sha='802d32bb4a7e3a7930a4809edf504e0ca016355303752d6d6236a03ec8e18b44'
assert os.geteuid()==0 and app.is_dir() and not app.is_symlink()
assert envfile.is_file() and not envfile.is_symlink() and envfile.stat().st_mode & 0o777==0o600
def sha(raw):return hashlib.sha256(raw).hexdigest()
def run(args):subprocess.run(args,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=40)
def download(name):
    with urllib.request.urlopen('https://raw.githubusercontent.com/huier5635-cmd/heguang-evidence-memory/'+commit+'/'+name,timeout=40) as response:raw=response.read(100001)
    assert len(raw)<=100000
    return raw
raw=download('deploy/dev6-observability-manifest.json');assert sha(raw)==manifest_sha
manifest=json.loads(raw);assert manifest['version']=='0.2.0.dev6+obs1' and len(manifest['base_files_sha256'])==10
for name,digest in manifest['base_files_sha256'].items():
    assert re.fullmatch(r'heguang_code/[a-z_]+\.py',name)
    path=app/name;path.resolve().relative_to(app.resolve());assert not path.is_symlink()
    assert sha(path.read_bytes())==digest,'Installed dev6 source mismatch'
new={}
assert set(manifest['files_sha256'])=={'heguang_code/__init__.py','heguang_code/api.py','heguang_code/request_metadata.py','pyproject.toml'}
for name,digest in manifest['files_sha256'].items():
    raw=download(name);assert sha(raw)==digest
    if name.endswith('.py'):compile(raw,name,'exec')
    target=app/name;target.resolve().relative_to(app.resolve());assert not target.is_symlink()
    new[name]=raw
assert not (app/'heguang_code/request_metadata.py').exists(),'Diagnostic source already exists; inspect rather than repeat'
oldenv=envfile.read_bytes();settings=dict(line.split('=',1) for line in oldenv.decode().splitlines() if '=' in line and not line.startswith('#'))
key=settings['HCM_API_KEY'];assert len(key)>=32
def request(path,body=None,key_value=None):
    headers={'Content-Type':'application/json'}
    if key_value is not None:headers['Authorization']='Bearer '+key_value
    data=None if body is None else json.dumps(body).encode()
    req=urllib.request.Request('http://127.0.0.1:8752/'+path,data=data,headers=headers)
    try:
        with urllib.request.urlopen(req,timeout=90) as response:return response.status,json.load(response)
    except urllib.error.HTTPError as error:return error.code,None
def ready(version):
    for _ in range(30):
        try:
            code,value=request('health')
            if code==200 and value['version']==version:return value
        except (OSError,ValueError,KeyError):pass
        time.sleep(1)
    raise RuntimeError('Coding health did not reach expected version')
before_health=ready('0.2.0.dev6')
paths=[envfile,pathlib.Path('/etc/caddy/Caddyfile'),pathlib.Path('/etc/systemd/system/heguang-memory.service'),pathlib.Path('/etc/systemd/system/heguang-coding.service')]
before_config={str(p):sha(p.read_bytes()) for p in paths}
user='obs-synthetic-'+uuid.uuid4().hex
body={'user_id':user,'session_id':'synthetic','request_id':'obs-initial','messages':[{'role':'assistant','content':'def cache_entry(key):\n    return registry.get(key)\n'+'# cache_entry preserves bounded source evidence\n'*110}]}
query={'user_id':user,'query':'cache_entry bounded source evidence','top_k':100}
assert request('add',body,key)[0]==200
code,prior=request('search',query,key);assert code==200 and prior['data']
stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ');journal_since=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
backup=base/'backups'/('dev6-observability-'+stamp);backup.mkdir(parents=True,mode=0o700,exist_ok=False)
old={name:(app/name).read_bytes() if (app/name).exists() else None for name in new}
for name,content in old.items():
    if content is not None:
        target=backup/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(content)
(backup/'plan.json').write_text(json.dumps({'commit':commit,'manifest_sha256':manifest_sha,'before_config_sha256':before_config,'full_allowed':False},indent=2)+'\n')
changed=False
try:
    run(['systemctl','stop',unit]);changed=True
    for name,content in new.items():(app/name).write_bytes(content);(app/name).chmod(0o644)
    run(['systemctl','start',unit]);health=ready('0.2.0.dev6+obs1')
    assert {k:v for k,v in health.items() if k!='version'}=={k:v for k,v in before_health.items() if k!='version'}
    assert request('search',query,key)==(200,prior),'Search response changed'
    assert request('add',body,key)[0]==200
    assert request('add',body)[0]==401
    assert request('search',query)[0]==401
    assert request('search',{**query,'user_id':user+'-empty'},key)==(200,{'data':[]})
    assert request('search',{},key)[0]==422
    assert request('add',{**body,'messages':[{'role':'user','content':'synthetic conflict'}]},key)[0]==409
    run(['systemctl','restart',unit]);ready('0.2.0.dev6+obs1')
    assert request('search',query,key)==(200,prior),'Persistent source changed after restart'
    assert {str(p):sha(p.read_bytes()) for p in paths}==before_config
    for name,digest in manifest['base_files_sha256'].items():
        if name not in new:assert sha((app/name).read_bytes())==digest
    records=[]
    for _ in range(2):
        result=subprocess.run(['journalctl','-u',unit,'--since',journal_since,'--output=cat','--no-pager'],check=True,capture_output=True,text=True,timeout=15)
        records=[json.loads(line.split('QIANORA_REQUEST ',1)[1]) for line in result.stdout.splitlines() if 'QIANORA_REQUEST ' in line]
        if {200,401,409,422}<={r.get('status') for r in records}:break
        time.sleep(1)
    allowed={'operation','status','completed','elapsed_ms','request_bytes','response_bytes','messages','rows','characters'}
    assert records and all(set(r)<=allowed for r in records)
    assert {200,401,409,422}<={r.get('status') for r in records}
    assert any(r.get('rows',0)>0 for r in records) and any(r.get('rows')==0 for r in records)
    receipt={'complete':True,'commit':commit,'version':health['version'],'backup_directory':str(backup),
             'original_modules_verified':10,'retrieval_storage_and_schema_unchanged':True,'exact_search_response_equal':True,
             'persistence_after_restart_passed':True,'metadata_statuses_verified':[200,401,409,422],
             'only_counter_fields_in_observed_metadata':True,'config_and_existing_key_unchanged':True,
             'synthetic_only':True,'official_smoke_started':False,'full_allowed':False}
    (base/'DEV6_OBSERVABILITY_UPGRADE.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt),flush=True)
except BaseException:
    if changed:
        run(['systemctl','stop',unit])
        for name,content in old.items():
            target=app/name
            if content is None:
                if target.exists():target.unlink()
            else:target.write_bytes(content)
        run(['systemctl','start',unit]);ready('0.2.0.dev6')
    print(json.dumps({'complete':False,'rolled_back':changed,'full_allowed':False}),flush=True)
    raise RuntimeError('Diagnostic upgrade failed; inspect protected server state') from None
QIANORA_OBSERVABILITY_UPGRADE
