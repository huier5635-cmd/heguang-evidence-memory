import asyncio
import hashlib
import json
from pathlib import Path
import sys

import httpx
import pytest
from fastapi.testclient import TestClient

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(KIT/'source'))
sys.path.insert(0,str(KIT/'deployment'))
sys.path.insert(0,str(KIT))
from heguang_code.api import create_app as original_app
from heguang_code.config import Settings
from observed_api import COUNTS,MAX_RESPONSE_CAPTURE,MetadataMiddleware,create_app as observed_create_app

from request_lifecycle import CompleteLargeRequest

def create_app(*args,**kwargs):
    return CompleteLargeRequest(observed_create_app(*args,**kwargs),sink=lambda record:None)

HEADERS = {'X-Api-Key':'synthetic-counter-check'}
ALLOWED = {'operation','status','completed','elapsed_ms','request_bytes','response_bytes','messages','rows','characters'}


class Score:
    def scores(self,query,documents):
        return [-float(i) for i in range(len(documents))]


def settings(path):
    return Settings(data_dir=path,api_key=HEADERS['X-Api-Key'],source_selection='coalesce')


def body():
    return {'user_id':'private-user-identity','session_id':'private-session','request_id':'private-request',
        'messages':[{'role':'user','content':'Private issue context fixture_marker'},
        {'role':'assistant','content':'def observed_value(key):\n    return fixture_marker(key)\n'*110}]}


def test_exact_response_and_unchanged_persistence_with_real_add_search(tmp_path):
    data = body()
    query = {'user_id':data['user_id'],'query':'fixture_marker observed_value private query','top_k':100}
    base = original_app(settings(tmp_path/'db'),scorer=Score())
    with TestClient(base) as client:
        added = client.post('/add',json=data,headers=HEADERS)
        before = client.post('/search',json=query,headers=HEADERS)
        assert added.status_code == before.status_code == 200 and before.json()['data']
    database = base.state.store.path(data['user_id'])
    checksum = hashlib.sha256(database.read_bytes()).hexdigest()
    records = []
    observed = create_app(settings(tmp_path/'db'),scorer=Score(),metadata_sink=records.append)
    with TestClient(observed) as client:
        after = client.post('/search',json=query,headers=HEADERS)
        assert after.status_code == 200 and after.content == before.content
        assert hashlib.sha256(database.read_bytes()).hexdigest() == checksum
        repeated = client.post('/add',json=data,headers=HEADERS)
        assert repeated.content == added.content
        assert client.post('/add',json={**data,'messages':[{'role':'user','content':'conflicting source'}]},headers=HEADERS).status_code == 409
        assert client.post('/add',json=data).status_code == 401
        assert client.post('/search',json={},headers=HEADERS).status_code == 422
        assert client.post('/search',json={**query,'user_id':'empty'},headers=HEADERS).json() == {'data':[]}
        assert client.get('/health').json()['version'] == '0.2.0.dev56'
    assert {200,401,409,422} <= {r['status'] for r in records}
    assert records[0]['rows'] == len(before.json()['data'])
    assert records[0]['characters'] == sum(len(r['content']) for r in before.json()['data'])
    assert records[1]['messages'] == 2 and records[2]['messages'] == 1
    assert records[1]['request_bytes'] == len(repeated.request.content)
    assert all(set(r)<=ALLOWED and r['completed'] for r in records)
    encoded = json.dumps(records)
    for private in ['private-user','private-session','private-request','fixture_marker','private query',HEADERS['X-Api-Key']]:
        assert private not in encoded
    assert COUNTS.get() is None


def test_concurrent_contexts_propagate_to_thread_and_do_not_mix(tmp_path):
    records = []
    app = create_app(settings(tmp_path),scorer=Score(),metadata_sink=records.append)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://synthetic',headers=HEADERS) as client:
            requests = [{'user_id':str(i),'request_id':'r','session_id':'s',
                'messages':[{'role':'user','content':('def needle_'+str(i)+'():\n    return 1\n')*100} for _ in range(i)]} for i in range(1,7)]
            replies = await asyncio.gather(*(client.post('/add',json=r) for r in requests))
            assert all(r.status_code==200 for r in replies)
            searches = await asyncio.gather(*(client.post('/search',json={'user_id':str(i),'query':'needle_'+str(i),'top_k':100}) for i in range(1,7)))
            assert all(r.status_code==200 and r.json()['data'] for r in searches)
            expected = sorted((len(r.json()['data']),sum(len(row['content']) for row in r.json()['data'])) for r in searches)
            assert sorted((r['rows'],r['characters']) for r in records if r['operation']=='search') == expected
    asyncio.run(run())
    assert sorted(r['messages'] for r in records if r['operation']=='add') == list(range(1,7))
    assert len(records)==12 and COUNTS.get() is None


def test_journal_failure_does_not_change_http_result(tmp_path):
    def unavailable(record):
        raise RuntimeError('synthetic unavailable sink')
    app = create_app(settings(tmp_path),scorer=Score(),metadata_sink=unavailable)
    with TestClient(app) as client:
        data = body()
        assert client.post('/add',json=data,headers=HEADERS).status_code==200
        assert client.post('/search',json={'user_id':data['user_id'],'query':'fixture_marker','top_k':100},headers=HEADERS).json()['data']


def test_forwarded_frames_and_original_send_error_survive_diagnostics():
    frames = [{'type':'http.response.start','status':200,'headers':[(b'content-type',b'application/json')]},
        {'type':'http.response.body','body':b'{"data":[]}', 'more_body':False}]
    seen,records = [],[]
    async def app(scope,receive,send):
        for frame in frames:
            await send(frame)
    async def receive():
        return {'type':'http.request','body':b'', 'more_body':False}
    async def send(frame):
        seen.append(frame)
        if frame['type']=='http.response.body':
            raise RuntimeError('original transport failure')
    middleware = MetadataMiddleware(app,sink=records.append)
    with pytest.raises(RuntimeError,match='original transport failure'):
        asyncio.run(middleware({'type':'http','path':'/search'},receive,send))
    assert all(a is b for a,b in zip(seen,frames))
    assert not records[0]['completed'] and 'rows' not in records[0]
    assert COUNTS.get() is None


def test_oversize_search_response_keeps_original_bytes_and_omits_counts():
    body = b'x'*(MAX_RESPONSE_CAPTURE+1)
    seen,records = [],[]
    async def app(scope,receive,send):
        await send({'type':'http.response.start','status':200,'headers':[]})
        for start in range(0,len(body),65536):
            await send({'type':'http.response.body','body':body[start:start+65536],'more_body':start+65536<len(body)})
    async def receive():
        return {'type':'http.request','body':b'', 'more_body':False}
    async def send(message):
        if message['type']=='http.response.body':seen.append(message['body'])
    asyncio.run(MetadataMiddleware(app,sink=records.append)({'type':'http','path':'/search'},receive,send))
    assert b''.join(seen)==body
    assert records[0]['response_bytes']==len(body) and records[0]['completed']
    assert 'rows' not in records[0] and 'characters' not in records[0]
