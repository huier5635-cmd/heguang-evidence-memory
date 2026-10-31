"""Stream the same trusted messages array to16 isolated synthetic users."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time

import httpx


def array_view(canonical_body):
    # The caller created this exact four-key envelope with messages LAST by
    # json.dumps. This is not a validator/parser for arbitrary external JSON.
    position = canonical_body.index(b',"messages":') + len(b',"messages":')
    if not canonical_body[position:position+1] == b'[' or not canonical_body.endswith(b']}'):
        raise ValueError('Unexpected own canonical replay envelope')
    return memoryview(canonical_body)[position:-1]


def request_parts(messages, envelope, *, chunk_bytes=65536):
    if set(envelope) != {'user_id', 'request_id', 'session_id'}:
        raise ValueError('Unexpected synthetic envelope keys')
    if type(chunk_bytes) is not int or not 1 <= chunk_bytes <= 65536:
        raise ValueError('Invalid replay chunk size')
    prefix = json.dumps(envelope, ensure_ascii=False, separators=(',', ':')).encode()[:-1] + b',"messages":'
    length = len(prefix) + len(messages) + 1
    def parts():
        yield prefix
        for start in range(0, len(messages), chunk_bytes):
            yield messages[start:start+chunk_bytes].tobytes()
        yield b'}'
    return length, parts()


def replay(base_url, key, body, session):
    messages = array_view(body)
    barrier = threading.Barrier(16)
    lock = threading.Lock()
    active, peak = 0, 0

    def one(index):
        nonlocal active, peak
        envelope = {'user_id': 'parallel-public-' + str(index).zfill(2),
                    'request_id': 'parallel-request-' + str(index).zfill(2), 'session_id': session}
        length, parts = request_parts(messages, envelope)
        with httpx.Client(base_url=base_url, timeout=285, trust_env=False,
                          headers={'X-Api-Key': key}) as client:
            barrier.wait(timeout=15)
            with lock:
                active += 1
                peak = max(peak, active)
            started = time.monotonic()
            try:
                response = client.post('/add', content=parts,
                    headers={'Content-Type': 'application/json', 'Content-Length': str(length)})
                if response.status_code != 200 or response.json() != {'success': True, **envelope}:
                    raise RuntimeError('Parallel synthetic Add failed')
                return {**envelope, 'http_status': response.status_code, 'request_bytes': length,
                        'seconds': round(time.monotonic()-started, 3)}
            finally:
                with lock:
                    active -= 1

    started = time.monotonic()
    results = []
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(one, i) for i in range(16)]
        for future in as_completed(futures):
            results.append(future.result())
    if len(results) != 16 or peak != 16:
        raise RuntimeError('Did not exercise all16 simultaneous synthetic client calls')
    return {'users': sorted(results, key=lambda r: r['user_id']), 'clients_peak': peak,
            'seconds': round(time.monotonic()-started, 3), 'messages_buffer_shared': True,
            'max_stream_chunk_bytes': 65536}


def concurrent_search(base_url, key, requests, verify):
    """Sixteen real HTTP searches, with validation outside the service cgroup."""
    assert len(requests) == 16
    barrier, lock = threading.Barrier(16), threading.Lock()
    active, peak = 0, 0

    def one(index, request):
        nonlocal active, peak
        with httpx.Client(base_url=base_url,timeout=285,trust_env=False,headers={'X-Api-Key':key}) as client:
            barrier.wait(timeout=15)
            with lock:
                active += 1
                peak = max(peak,active)
            started = time.monotonic()
            try:
                response = client.post('/search',json=request)
                assert response.status_code == 200
                data = response.json()['data']
                assert 1 <= len(data) <= 100
                assert sum(len(row['content']) for row in data) <= 128000
                verify(request, response)
                return {'index':index,'http_status':200,'rows':len(data),
                        'response_sha256':hashlib.sha256(response.content).hexdigest(),
                        'response_bytes':len(response.content),'seconds':round(time.monotonic()-started,3)}
            finally:
                with lock:
                    active -= 1

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(one,i,request) for i,request in enumerate(requests)]
        results = [future.result() for future in as_completed(futures)]
    assert peak == 16 and len(results) == 16
    return {'clients_peak':peak,'seconds':round(time.monotonic()-started,3),
            'requests':sorted(results,key=lambda r:r['index'])}


def replay_search(base_url, key, query, expected):
    def verify(request, response):
        assert response.content == expected
    result = concurrent_search(base_url,key,[query.copy() for _ in range(16)],verify)
    result['all_responses_byte_equal'] = True
    result['static_call_query_characters'] = len(query['query'])
    return result


def replay_history_search(base_url, key, users, database_directory):
    from heguang_code.config import Settings
    from heguang_code.selection import source_record
    from heguang_code.store import Store
    store = Store(Settings(data_dir=Path(database_directory),api_key=key))
    requests = [{'user_id':u['user_id'],'query':'Qiskit QuantumCircuit circuit append','top_k':100} for u in users]
    assert len({r['user_id'] for r in requests}) == 16

    def verify(request, response):
        path = store.path(request['user_id'])
        with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as db:
            db.row_factory = sqlite3.Row
            for row in response.json()['data']:
                meta, body = source_record(row)
                assert store.source_span(db,meta['source_id'],*meta['characters'],{}) == body
    result = concurrent_search(base_url,key,requests,verify)
    result.update(independent_users=16,returned_source_spans_audited=16,read_only_databases=True)
    return result
