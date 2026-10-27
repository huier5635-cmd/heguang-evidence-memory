"""One real HTTP replay, with only the service inside a strict Linux cgroup.

The parent holds reference data outside the service budget. Docker bounds the
actual application, including ONNX and every child, rather than a client shim.
"""
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import zlib
from parallel_replay import replay

import httpx

KIT = Path(__file__).resolve().parent
OUT = KIT/'capacity-result'
KEY = 'synthetic-public-history-probe-only'


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def docker(*arguments, check=True):
    return subprocess.run(['docker',*arguments],check=check,capture_output=True,text=True,timeout=30)


def reference_digest(value):
    checksum = hashlib.sha256()
    for text in json.JSONEncoder(ensure_ascii=False,sort_keys=True,separators=(',',':')).iterencode(value):
        for start in range(0,len(text),16384):
            checksum.update(text[start:start+16384].encode())
    return checksum.hexdigest()


def audit_digest(compressed):
    decoder,checksum,count = zlib.decompressobj(),hashlib.sha256(),0
    while compressed:
        raw = decoder.decompress(compressed,65536)
        checksum.update(raw)
        count += len(raw)
        compressed = decoder.unconsumed_tail
    assert decoder.eof and not decoder.unused_data
    return checksum.hexdigest(),count


def snapshot(name):
    counters = json.loads(docker('exec',name,'python','/kit/cgroup_snapshot.py').stdout)
    assert int(counters['memory.max']) == 384*1024**2
    assert int(counters['memory.high']) == 320*1024**2
    assert int(counters['memory.swap.max']) == 512*1024**2
    quota,period = map(int,counters['cpu.max'].split())
    assert quota == period and int(counters['pids.max']) == 64
    assert int(dict(line.split() for line in counters['memory.events'].splitlines())['oom_kill']) == 0
    return counters


def main():
    assert sys.platform == 'linux'
    image = os.environ['QIANORA_CAPACITY_IMAGE']
    assert image.startswith('sha256:') and len(image) == 71
    OUT.mkdir(exist_ok=False)
    cli_version = docker('version','--format','{{.Client.Version}}').stdout.strip()
    daemon_version = docker('version','--format','{{.Server.Version}}').stdout.strip()
    cgroup_version = docker('info','--format','{{.CgroupVersion}}').stdout.strip()
    cgroup_driver = docker('info','--format','{{.CgroupDriver}}').stdout.strip()
    print(json.dumps({'docker_client':cli_version,'docker_server':daemon_version,'cgroup_version':cgroup_version,'cgroup_driver':cgroup_driver}),flush=True)
    assert cgroup_version == '2'
    assets = json.loads((KIT/'inputs/result.json').read_text(encoding='utf-8'))
    row = assets['files'][0]
    original_path = KIT/'inputs'/row['local_path']
    assert sha(original_path) == row['sha256']
    original = json.loads(original_path.read_bytes())
    user,request_id = 'large-public-history',row['path']
    payload = {'user_id':user,'request_id':request_id,'session_id':row['path'].split('/')[-2],
               'messages':original['messages']}
    body = json.dumps(payload,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()
    assert 64*1024**2 < len(body) <= 128*1024**2
    sys.path.insert(0,str(KIT/'source'))
    from heguang_code.schema import AddRequest
    validated = AddRequest.model_validate(payload)
    largest = max(range(len(validated.messages)),key=lambda i:len(validated.messages[i].extra.get('raw_output','')))
    expected = reference_digest(validated.messages[largest].model_dump(exclude_unset=True))
    del validated
    sources = {p:sha(p) for p in (KIT/'source/heguang_code').glob('*.py')}
    sources[Path(__file__)] = sha(__file__)
    sources[KIT/'deployment/observed_api.py'] = sha(KIT/'deployment/observed_api.py')
    sources[KIT/'parallel_replay.py'] = sha(KIT/'parallel_replay.py')
    sources[KIT/'snapshot_host.py'] = sha(KIT/'snapshot_host.py')
    sources[KIT/'deployment/request_lifecycle.py'] = sha(KIT/'deployment/request_lifecycle.py')
    plan = {'scope':'Linux cgroup service capacity, not actual cloud deployment or repair score',
        'source_sha256':{str(p.relative_to(KIT)):h for p,h in sources.items()},'history_sha256':row['sha256'],
        'image':image,'request_bytes':len(body),'memory_max':384*1024**2,'memory_high':320*1024**2,
        'swap_max':512*1024**2,'cpu_cores':1,'pids_max':64,'parent_reference_data_outside_service_budget':True,
        'target_tests_or_solutions_read':False,'official_calls':0,'full_allowed':False}
    plan['lifecycle_extension'] = {'identifier':'complete-large-asgi-cycle1','core_changed':False,'hypothesis':'Retained completed ASGI request cycles may accumulate across serialized large Add calls; whole-request admission plus explicit cyclic collection is tested together, not as separate causal effects','request_timeout_seconds':285,'total_service_wall_seconds':300}
    plan['parallel_followup'] = {'concurrent_clients':16,'independent_users':16,
        'same_original_history_per_user':True,'max_client_stream_chunk_bytes':65536,
        'service_limits_unchanged':True,'full_suite_capacity_claimed':False}
    (OUT/'frozen-plan.json').write_text(json.dumps(plan,indent=2)+'\n',encoding='utf-8')
    worker_source = ('from observed_api import create_app\nfrom request_lifecycle import CompleteLargeRequest\nfrom heguang_code.config import Settings\n'
        'from pathlib import Path\napp=create_app(Settings(data_dir=Path("/output/db"),api_key='+repr(KEY)+
        ',reranker_dir=Path("/kit/model"),reranker_threads=1,source_selection="coalesce",max_concurrency=1))\napp=CompleteLargeRequest(app)\n')
    (OUT/'large_worker.py').write_text(worker_source,encoding='utf-8')
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1',0))
        port = reservation.getsockname()[1]
    name = 'qianora-dev17-cycle1-'+sha(OUT/'frozen-plan.json')[:16]
    assert docker('inspect',name,check=False).returncode != 0
    command = ['docker','run','--name',name,'--read-only','--cap-drop=ALL','--security-opt=no-new-privileges',
        '--cpus=1','--memory=384m','--memory-swap=896m','--pids-limit=64',
        '--user',str(os.getuid())+':'+str(os.getgid()),'--tmpfs','/tmp:rw,nosuid,noexec,size=16m',
        '--publish',f'127.0.0.1:{port}:{port}','--mount','type=bind,src='+str(KIT)+',dst=/kit,readonly',
        '--mount','type=bind,src='+str(OUT)+',dst=/output','--workdir','/output',
        '--env','PYTHONPATH=/kit/source:/kit/deployment','--env','PYTHONDONTWRITEBYTECODE=1',
        '--env','TOKENIZERS_PARALLELISM=false','--env','OMP_NUM_THREADS=1','--env','OPENBLAS_NUM_THREADS=1',
        image,'python','-m','uvicorn','large_worker:app','--app-dir','/output','--host','0.0.0.0',
        '--port',str(port),'--workers','1','--no-access-log','--log-level','warning']
    started = time.monotonic()
    result = {'complete':False,'failure':None,'official_calls':0,'full_allowed':False,
        'cloud_capacity_proven':False,'target_tests_or_solutions_read':False}
    with (OUT/'worker.log').open('xb') as log:
        child = subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL)
        done = threading.Event()
        def deadline():
            if not done.wait(300):
                result['guard_reason'] = '300-second service wall limit'
                docker('stop','--time','5',name,check=False)
        guard = threading.Thread(target=deadline,daemon=True)
        guard.start()
        try:
            with httpx.Client(base_url=f'http://127.0.0.1:{port}',headers={'X-Api-Key':KEY},timeout=150,trust_env=False) as client:
                for _ in range(100):
                    if child.poll() is not None:
                        raise RuntimeError('Strict cgroup service startup failed')
                    try:
                        health_response = client.get('/health',timeout=2)
                        if health_response.status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(.1)
                else:
                    raise RuntimeError('Strict cgroup service not ready')
                health = health_response.json()
                assert health['version'] == '0.2.0.dev17' and health['max_body_bytes'] == 128*1024**2
                result['health'] = health
                inspected_running = json.loads(docker('inspect',name).stdout)[0]
                configured = subprocess.run(['sudo','-n','/usr/bin/python3',str(KIT/'configure_high.py'),'--pid',str(inspected_running['State']['Pid']),'--container-id',inspected_running['Id']],check=True,capture_output=True,text=True,timeout=15)
                result['startup_high_configuration'] = json.loads(configured.stdout)
                result['actual_initial_cgroup'] = snapshot(name)
                warm = {'user_id':'warm','session_id':'synthetic','request_id':'warm',
                        'messages':[{'role':'user','content':'def warm_lifecycle_cache(key):\n    return key\n'*100}]}
                assert client.post('/add',json=warm).status_code == 200
                query = {'user_id':'warm','query':'warm_lifecycle_cache','top_k':100}
                before = client.post('/search',json=query)
                assert before.status_code == 200 and before.json()['data']
                t = time.monotonic()
                added = client.post('/add',content=body,headers={'Content-Type':'application/json'})
                result.update(large_add_seconds=round(time.monotonic()-t,3),large_add_http_status=added.status_code)
                assert added.status_code == 200 and added.json()['request_id'] == request_id
                after = client.post('/search',json=query)
                assert after.status_code == 200 and after.content == before.content
                result['model_search_before_after_equal'] = True
                parallel = replay(f'http://127.0.0.1:{port}',KEY,body,payload['session_id'])
                result['parallel_replay'] = parallel
                final_search = client.post('/search',json=query)
                assert final_search.status_code == 200 and final_search.content == before.content
                result['model_search_after_parallel_equal'] = True
                result['actual_final_cgroup'] = snapshot(name)
                path = OUT/'db'/(hashlib.sha256(user.encode()).hexdigest()+'.sqlite3')
                with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as db:
                    assert db.execute('SELECT count(*) FROM sources').fetchone()[0] == len(original['messages']) == 87
                    identity = hashlib.sha256(json.dumps([user,request_id,largest],ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:40]
                    compressed,digest = db.execute('SELECT body,digest FROM raw_messages WHERE source_id=?',(identity,)).fetchone()
                    recovered,audit_bytes = audit_digest(compressed)
                    assert recovered == digest == expected
                    chars = db.execute('SELECT chars FROM sources WHERE id=?',(identity,)).fetchone()[0]
                    assert chars < 32768 and db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
                    result.update(messages_saved=87,largest_audit_bytes=audit_bytes,largest_audit_sha256=recovered,
                        largest_searchable_chars=chars,full_original_audit_retained=True)
                warm_path = OUT/'db'/(hashlib.sha256(b'warm').hexdigest()+'.sqlite3')
                with sqlite3.connect(warm_path.resolve().as_uri()+'?mode=ro',uri=True) as warm_db:
                    assert warm_db.execute('SELECT count(*) FROM episode_docs').fetchone()[0] == 1
                    assert warm_db.execute('SELECT count(*) FROM episode_search').fetchone()[0] == 1
                    assert not warm_db.execute('PRAGMA foreign_key_check').fetchall()
                result['episode_index_verified'] = True
                for replayed in parallel['users']:
                    replay_path = OUT/'db'/(hashlib.sha256(replayed['user_id'].encode()).hexdigest()+'.sqlite3')
                    with sqlite3.connect(replay_path.resolve().as_uri()+'?mode=ro',uri=True) as replay_db:
                        assert replay_db.execute('SELECT count(*) FROM sources').fetchone()[0] == 87
                        assert replay_db.execute('SELECT count(*) FROM writes').fetchone()[0] == 1
                        replay_identity = hashlib.sha256(json.dumps([replayed['user_id'],replayed['request_id'],largest],
                            ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:40]
                        compressed,digest = replay_db.execute('SELECT body,digest FROM raw_messages WHERE source_id=?',
                                                              (replay_identity,)).fetchone()
                        recovered,audit_bytes = audit_digest(compressed)
                        assert recovered == digest == expected
                        assert replay_db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
                        assert not replay_db.execute('PRAGMA foreign_key_check').fetchall()
                result['parallel_users_audit_verified'] = 16
                result['complete'] = True
        except Exception as error:
            result['failure'] = {'type':type(error).__name__,'message':str(error)[:200]}
        finally:
            done.set()
            guard.join(1)
            try:
                live_state = json.loads(docker('inspect',name).stdout)[0]
                if live_state['State']['Running']:
                    diagnostic = subprocess.run(['sudo','-n','/usr/bin/python3',str(KIT/'snapshot_host.py'),
                        '--pid',str(live_state['State']['Pid']),'--container-id',live_state['Id']],
                        check=True,capture_output=True,text=True,timeout=15)
                    result['before_cleanup_cgroup'] = json.loads(diagnostic.stdout)
            except Exception as diagnostic_error:
                result['before_cleanup_cgroup_error_type'] = type(diagnostic_error).__name__
            docker('stop','--time','5',name,check=False)
            try:
                child.wait(10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(5)
            inspected = docker('inspect',name,check=False)
            state = json.loads(inspected.stdout)[0]['State'] if inspected.returncode == 0 else None
            log.flush()
            worker_tail = (OUT/'worker.log').read_bytes()[-8192:].decode('utf-8',errors='replace')
            if result['complete']:
                try:
                    records = [json.loads(line.split('QIANORA_REQUEST ',1)[1]) for line in worker_tail.splitlines() if 'QIANORA_REQUEST ' in line]
                    allowed = {'operation','status','completed','elapsed_ms','request_bytes','response_bytes','messages','rows','characters'}
                    assert len(records)==21 and all(set(r)<=allowed and r['completed'] and r['status']==200 for r in records)
                    assert sorted(r['messages'] for r in records if r['operation']=='add')==[1]+[87]*17
                    assert len([r for r in records if r['operation']=='search' and r.get('rows',0)>0])==3
                    result['only_counter_metadata_verified'] = True
                    result['metadata_records'] = records
                    lifecycle = [json.loads(line.split('QIANORA_LIFECYCLE ',1)[1]) for line in worker_tail.splitlines() if 'QIANORA_LIFECYCLE ' in line]
                    assert len(lifecycle)==17 and all(set(r)=={'collected','seconds'} and type(r['collected']) is int and r['collected']>=0 for r in lifecycle)
                    result['complete_request_lifecycle'] = lifecycle
                    result['cycle_collection_total'] = sum(r['collected'] for r in lifecycle)
                except Exception as error:
                    result['complete'] = False
                    result['failure'] = {'type':type(error).__name__,'message':'Metadata integration verification failed'}
            if result['failure']:
                result['worker_stderr_tail'] = worker_tail
                result['docker_inspect_stderr'] = inspected.stderr[-2000:]
                result['docker_client_returncode'] = child.returncode
            result['container_terminal_state'] = state
            result['service_reaped'] = bool(state and not state['Running'])
            result['sources_unchanged'] = all(sha(p)==h for p,h in sources.items()) and sha(original_path)==row['sha256']
            result['seconds'] = round(time.monotonic()-started,3)
            result['complete'] = bool(result['complete'] and result['service_reaped'] and result['sources_unchanged'] and not state['OOMKilled'] and not result.get('guard_reason'))
            (OUT/'result.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
            print('QIANORA_DEV17_CYCLE1_CAPACITY '+json.dumps(result,sort_keys=True),flush=True)
            docker('rm',name,check=False)
    if not result['complete']:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
