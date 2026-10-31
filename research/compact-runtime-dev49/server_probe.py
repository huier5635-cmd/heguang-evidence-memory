"""Bounded-memory staging verification; never invokes official evaluations."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
import uuid
import zlib

from pinned_array import messages_bounds,replay_parts

HISTORY_SHA = '78b39bf8393b3e7be15084e36721ad8f36ee569f4b1685251a85c4618394a214'
AUDIT_SHA = '9ae5d995262fc03d31f082ce61ac41041eb8f9d00e6aeff1f9421290a8cace83'
ALLOWED = {'operation','status','completed','elapsed_ms','request_bytes','response_bytes','messages','rows','characters','rerank_documents','compact_contexts','compact_context_tokens'}


def cgroup_snapshot(unit):
    control = subprocess.run(['systemctl','show',unit,'--property=ControlGroup','--value'],
        check=True,capture_output=True,text=True,timeout=10).stdout.strip()
    root = Path('/sys/fs/cgroup').resolve()
    group = (root/control.lstrip('/')).resolve()
    assert group!=root and group.is_relative_to(root) and unit in group.parts
    names = ['memory.high','memory.max','memory.peak','memory.swap.max','memory.swap.peak','memory.events','cpu.max','pids.max']
    values = {name:(group/name).read_text().strip() if (group/name).exists() else None for name in names}
    assert int(values['memory.high'])==320*1024**2 and int(values['memory.max'])==384*1024**2
    assert int(values['memory.swap.max'])==512*1024**2 and int(values['pids.max'])==64
    quota,period = map(int,values['cpu.max'].split())
    assert quota==period and dict(line.split() for line in values['memory.events'].splitlines())['oom_kill']=='0'
    return values


def run(args):
    assert os.geteuid()==0
    release = Path(__file__).resolve().parent
    raw_manifest = (release/'runtime-manifest.json').read_bytes()
    identity = hashlib.sha256(raw_manifest).hexdigest()[:12]
    unit = 'heguang-coding-stage-dev45ctx49-'+identity+'.service'
    data_dir = Path('/var/lib/heguang-coding-stage-dev45ctx49-'+identity)
    assert args.source.is_file() and not args.source.is_symlink()
    env_path = Path('/etc/heguang-coding.env')
    assert env_path.is_file() and not env_path.is_symlink() and env_path.stat().st_mode&0o777 == 0o600
    env = dict(line.split('=',1) for line in env_path.read_text().splitlines() if '=' in line and not line.startswith('#'))
    key = env['HCM_API_KEY']
    assert len(key)>=32
    # The key stays in this server process, never in argv, logs or a new file.
    def request(path,body=None,auth=True,wire_length=None,raw=False):
        headers = {'Content-Type':'application/json'}
        if auth:headers['Authorization']='Bearer '+key
        if wire_length is not None:headers['Content-Length']=str(wire_length)
        data = body if wire_length is not None else (None if body is None else json.dumps(body).encode())
        req = urllib.request.Request('http://127.0.0.1:8756/'+path,data=data,headers=headers)
        try:
            with urllib.request.urlopen(req,timeout=150) as response:
                value = response.read(1024*1024+1)
                assert len(value)<=1024*1024
                return response.status,value if raw else json.loads(value)
        except urllib.error.HTTPError as error:
            return error.code,None

    health = request('health',auth=False)[1]
    assert health['version']=='0.2.0.dev45+ctx6x16k' and health['max_body_bytes']==128*1024**2
    assert health['reranker_threads']==health['max_processing_requests']==1
    bounds = messages_bounds(args.source,HISTORY_SHA)
    nonce = uuid.uuid4().hex
    warm = {'user_id':'stage-warm-'+nonce,'session_id':'synthetic','request_id':'warm',
        'messages':[{'role':'user','content':'def warm_lifecycle_cache(key):\n    return key\n'*100}]}
    query = {'user_id':warm['user_id'],'query':'warm_lifecycle_cache','top_k':100}
    assert request('add',warm)[0]==200
    code,before = request('search',query,raw=True)
    assert code==200 and json.loads(before)['data']
    user = 'stage-public-'+nonce
    envelope = {'user_id':user,'session_id':'qiskit__qiskit-terra-8741','request_id':'public-history'}
    wire_bytes,stream = replay_parts(args.source,bounds,envelope)
    started = time.monotonic()
    code,added = request('add',stream,wire_length=wire_bytes)
    assert code==200 and added['request_id']==envelope['request_id']
    seconds = round(time.monotonic()-started,3)
    assert request('search',query,raw=True)==(200,before)
    assert request('add',warm,auth=False)[0]==401
    assert request('search',query,auth=False)[0]==401
    assert request('search',{})[0]==422
    assert request('add',{**warm,'messages':[{'role':'user','content':'conflict'}]})[0]==409
    assert request('search',{**query,'user_id':'empty-'+nonce})==(200,{'data':[]})
    path = data_dir/(hashlib.sha256(user.encode()).hexdigest()+'.sqlite3')
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as db:
        assert db.execute('SELECT count(*) FROM sources').fetchone()[0]==87
        rows = db.execute('SELECT source_id,body FROM raw_messages WHERE digest=?',(AUDIT_SHA,)).fetchall()
        assert len(rows)==1
        source_id,compressed = rows[0]
        decoder,digest,count = zlib.decompressobj(),hashlib.sha256(),0
        while compressed:
            part = decoder.decompress(compressed,65536)
            digest.update(part);count+=len(part);compressed=decoder.unconsumed_tail
        assert decoder.eof and not decoder.unused_data and digest.hexdigest()==AUDIT_SHA and count==81060483
        assert db.execute('SELECT chars FROM sources WHERE id=?',(source_id,)).fetchone()[0]==10786
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
    before_restart_limits = cgroup_snapshot(unit)
    subprocess.run(['systemctl','restart',unit],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30)
    for _ in range(30):
        try:
            if request('health',auth=False)[1]['version']=='0.2.0.dev45+ctx6x16k':break
        except OSError:pass
        time.sleep(1)
    else:raise RuntimeError('Staging restart readiness failed')
    assert request('search',query,raw=True)==(200,before)
    after_restart_limits = cgroup_snapshot(unit)
    journal = subprocess.run(['journalctl','-u',unit,'--since',args.since,'--output=cat','--no-pager'],
        check=True,capture_output=True,text=True,timeout=15).stdout
    records = [json.loads(line.split('QIANORA_REQUEST ',1)[1]) for line in journal.splitlines() if 'QIANORA_REQUEST ' in line]
    assert records and all(set(row)<=ALLOWED for row in records)
    cycles = [json.loads(line.split('QIANORA_LIFECYCLE ',1)[1]) for line in journal.splitlines() if 'QIANORA_LIFECYCLE ' in line]
    assert cycles and all(set(row)=={'collected','seconds'} and type(row['collected']) is int and row['collected']>=0 and isinstance(row['seconds'],(int,float)) and row['seconds']>=0 for row in cycles)
    assert {200,401,409,422}<={row['status'] for row in records}
    assert any(row.get('messages')==87 and row['status']==200 and row['completed'] for row in records)
    assert any(row.get('rows',0)>0 for row in records) and any(row.get('rows')==0 for row in records)
    result = {'complete':True,'scope':'Actual cloud loopback staging only; no official or public production change',
        'messages_saved':87,'wire_bytes':wire_bytes,'large_add_seconds':seconds,'full_original_audit_sha256':AUDIT_SHA,
        'full_original_audit_bytes':81060483,'exact_model_reload_search_equal':True,'restart_persistence_passed':True,
        'metadata_statuses_verified':[200,401,409,422],'metadata_only_allowed_counters':True,'lifecycle_collection_counters_verified':True,
        'actual_cgroup_before_restart':before_restart_limits,'actual_cgroup_after_restart':after_restart_limits,
        'streamed_input_without_full_unicode_json_copy':True,'official_calls':0,'full_allowed':False}
    print(json.dumps(result,sort_keys=True),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--since',required=True)
    arguments = parser.parse_args()
    try:
        run(arguments)
    except Exception as error:
        print(json.dumps({'complete':False,'failure_type':type(error).__name__,'official_calls':0,'full_allowed':False}),flush=True)
        raise SystemExit(2)
