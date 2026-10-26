import asyncio
import importlib
import importlib.util
import json
from pathlib import Path
import sys
import pytest
from fastapi.testclient import TestClient
from heguang_code.api import create_app
from heguang_code.config import Settings
from heguang_code.request_metadata import RequestMetadataMiddleware


def baseline_module():
    folder=Path(__file__).resolve().parents[2]/'coding-memory-dev6/heguang_code'
    name='qianora_metadata_baseline'
    if name not in sys.modules:
        spec=importlib.util.spec_from_file_location(name,folder/'__init__.py',submodule_search_locations=[str(folder)])
        module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
    return importlib.import_module(name+'.api'),importlib.import_module(name+'.config')


def test_http_contract_matches_original_and_logs_only_counters(tmp_path):
    original,config=baseline_module();records=[]
    apps=[original.create_app(config.Settings(data_dir=tmp_path/'old',api_key='SECRET_KEY',max_body_bytes=1800)),
          create_app(Settings(data_dir=tmp_path/'new',api_key='SECRET_KEY',max_body_bytes=1800),metadata_sink=records.append)]
    results=[]
    body={'user_id':'SECRET_USER','session_id':'SECRET_SESSION','request_id':'SECRET_REQUEST',
          'messages':[{'role':'user','content':'SECRET_BODY cache_entry()'}]}
    query={'user_id':'SECRET_USER','query':'SECRET_QUERY cache_entry','top_k':5}
    for app in apps:
        with TestClient(app) as client:
            headers={'Authorization':'Bearer SECRET_KEY'}
            responses=[client.post('/add?private=SECRET_URL',json=body),
                       client.post('/add',json=body,headers=headers),
                       client.post('/add',json=body,headers=headers),
                       client.post('/search',json=query,headers=headers),
                       client.post('/search',json={**query,'user_id':'missing'},headers=headers),
                       client.post('/search',json={'bad':'SECRET_INVALID'},headers=headers),
                       client.post('/add',json={**body,'messages':[{'role':'user','content':'SECRET_CONFLICT'}]},headers=headers),
                       client.post('/add',content=b'x'*2000,headers=headers)]
            assert client.get('/health').status_code==200
            assert client.get('/unknown?SECRET_PATH').status_code==401
            results.append([(r.status_code,r.json()) for r in responses])
    assert results[0]==results[1]
    assert [r['status'] for r in records]==[401,200,200,200,200,422,409,413]
    assert [r.get('rows') for r in records[3:5]]==[1,0]
    assert records[1]['messages']==1 and all(r['completed'] for r in records)
    assert 'SECRET' not in json.dumps(records)
    allowed={'operation','status','completed','elapsed_ms','request_bytes','response_bytes','messages','rows','characters'}
    assert all(set(r)<=allowed for r in records)


def test_journal_failure_cannot_change_success_or_error_response(tmp_path):
    def broken_sink(record):raise RuntimeError('SECRET_SINK')
    app=create_app(Settings(data_dir=tmp_path,api_key='key'),metadata_sink=broken_sink)
    with TestClient(app) as client:
        assert client.post('/search',json={'user_id':'none','query':'q','top_k':1},headers={'X-Api-Key':'key'}).json()=={'data':[]}
        assert client.post('/search',json={}).status_code==401


def test_model_or_storage_failure_logs_status_without_exception_text(tmp_path):
    records=[]
    app=create_app(Settings(data_dir=tmp_path,api_key='key'),metadata_sink=records.append)
    def fail(*args,**kwargs):raise RuntimeError('SECRET_STORAGE_DETAIL')
    app.state.store.search=fail
    with TestClient(app) as client:
        response=client.post('/search',json={'user_id':'user','query':'SECRET_QUERY','top_k':1},headers={'X-Api-Key':'key'})
    assert response.status_code==503 and records[0]['status']==503
    assert records[0]['completed'] and 'SECRET' not in json.dumps(records)


def test_response_send_failure_is_not_reported_as_complete():
    records=[]
    async def upstream(scope,receive,send):
        await send({'type':'http.response.start','status':200,'headers':[]})
        await send({'type':'http.response.body','body':b'SECRET_BODY'})
    async def receive():return {'type':'http.disconnect'}
    async def send(message):
        if message['type']=='http.response.body':raise ConnectionError('SECRET_CONNECTION')
    middleware=RequestMetadataMiddleware(upstream,sink=records.append)
    with pytest.raises(ConnectionError):asyncio.run(middleware({'type':'http','path':'/search'},receive,send))
    assert records[0]['status']==200 and not records[0]['completed']
    assert 'SECRET' not in json.dumps(records)


def test_disconnect_or_original_exception_is_preserved_without_sensitive_details():
    records=[]
    failure=RuntimeError('SECRET_EXCEPTION')
    async def upstream(scope,receive,send):raise failure
    async def receive():return {'type':'http.request','body':b'SECRET_BODY'}
    async def send(message):pass
    middleware=RequestMetadataMiddleware(upstream,sink=records.append)
    with pytest.raises(RuntimeError) as caught:
        asyncio.run(middleware({'type':'http','path':'/search','query_string':b'SECRET_QUERY'},receive,send))
    assert caught.value is failure
    assert records[0]['status'] is None and not records[0]['completed']
    assert 'SECRET' not in json.dumps(records)
