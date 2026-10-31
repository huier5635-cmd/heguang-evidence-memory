"""Owned activation-gate and routing fixtures; no actual cloud/repair claims."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys

import pytest

WORK=Path(__file__).resolve().parents[1]
TEMPLATE=WORK/'dev42-public-query-template'
sys.path.insert(0,str(TEMPLATE))
from public_manifest import validate,PARENT_MANIFEST_SHA
from public_unit import render,locations
from routing import adapt,MAIN,QUERY

def bundle(tmp_path):
    root=tmp_path/'owned-fixture';root.mkdir()
    for path in TEMPLATE.glob('*.py'):shutil.copyfile(path,root/path.name)
    files={path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in root.glob('*.py')}
    manifest={'owned_fixture_only':True,'not_actual_cloud_or_native_evidence':True,
        'version':'0.2.0.dev32+ctx6x16k','scope':'official_smoke_diagnostic_only','full_allowed':False,
        'parent_manifest_sha256':PARENT_MANIFEST_SHA,'actual_cloud_profile_preflight_passed':True,
        'full_parallel_history_capacity_passed':True,'repair_benefit_demonstrated':False,'native_repair_gate_passed':False,
        'model_input_context_budget':{'records':6,'characters':16000},'candidate_core_modules':21,
        'files_sha256':files}
    for name in ['cloud_profile_receipt_sha256','public_entry_check_sha256',
                 'parent_review_sha256','whole_record_budget_source_sha256']:manifest[name]='a'*64
    manifest['native_repair_receipt_sha256']=None
    path=root/'public-manifest.json';path.write_text(json.dumps(manifest),encoding='utf-8')
    return root,path,manifest

@pytest.mark.parametrize('field,value',[
    ('scope','full'),('full_allowed',True),('version','unreviewed'),('parent_manifest_sha256','b'*64),
    ('actual_cloud_profile_preflight_passed',False),('full_parallel_history_capacity_passed',False),
    ('repair_benefit_demonstrated','unverified'),('repair_benefit_demonstrated',True),
    ('model_input_context_budget',{'records':100,'characters':128000}),('candidate_core_modules',20),
    ('cloud_profile_receipt_sha256','0'*64),('public_entry_check_sha256','missing')])
def test_unmet_activation_proofs_and_wrong_scope_are_rejected(tmp_path,field,value):
    root,path,manifest=bundle(tmp_path);manifest[field]=value;path.write_text(json.dumps(manifest),encoding='utf-8')
    with pytest.raises(ValueError):validate(root,hashlib.sha256(path.read_bytes()).hexdigest())

def test_changed_helpers_or_manifest_do_not_activate(tmp_path):
    root,path,manifest=bundle(tmp_path);digest=hashlib.sha256(path.read_bytes()).hexdigest()
    assert validate(root,digest)['owned_fixture_only']
    (root/'public_runner.py').write_text('changed source\n',encoding='utf-8')
    with pytest.raises(ValueError,match='helper changed'):validate(root,digest)
    with pytest.raises(ValueError,match='manifest differs'):validate(root,'0'*64)

def current_routes():
    spec=importlib.util.spec_from_file_location('owned_old_diagnostic_route',WORK/'dev25-public-diagnostic-template/routing.py')
    route=importlib.util.module_from_spec(spec);spec.loader.exec_module(route)
    spec=importlib.util.spec_from_file_location('owned_stable_route',WORK/'dev25_stable_endpoint_adapter.py')
    stable=importlib.util.module_from_spec(spec);spec.loader.exec_module(stable)
    raw=json.loads((WORK.parent/'outputs/dev25-public-routing-preparation.json').read_text('utf-8'))['redacted_caddyfile'].encode()
    diagnostic=route.add_route(raw)
    old_public=stable.adapt(diagnostic,hashlib.sha256(diagnostic).hexdigest())
    spec=importlib.util.spec_from_file_location('owned_previous_context_route',WORK/'dev39-public-context-template/routing.py')
    context=importlib.util.module_from_spec(spec);spec.loader.exec_module(context)
    return context.adapt(old_public,hashlib.sha256(old_public).hexdigest())

def test_new_query_route_preserves_current_context_canonical_and_all_old_routes():
    current=current_routes()
    assert hashlib.sha256(current).hexdigest()=='a139c679bbfe41c005d0ab74d7ef84cdbdd0e3d4504924b8e8d9cd8cd02783f0'
    result=adapt(current,hashlib.sha256(current).hexdigest())
    assert result.replace(QUERY+b'\n'+MAIN,MAIN,1)==current
    assert result.count(b'127.0.0.1:8757')==1 and result.count(b'127.0.0.1:8755')==2
    assert result.count(b'127.0.0.1:8753')==result.count(b'127.0.0.1:8752')==1
    for route in [b'/coding/context/*',b'/coding/dev6/*',b'/coding/dev25/*']:
        assert result.count(route)==current.count(route)==1
    assert b'@query_api path /health /add /search' in QUERY
    assert b'max_size 134217728' in QUERY and b'response_header_timeout 30m' in QUERY
    assert result.split(b'\t@memory',1)[1]==current.split(b'\t@memory',1)[1]

def test_repeated_unexpected_or_wrongly_bound_routes_are_rejected():
    current=current_routes()
    with pytest.raises(ValueError):adapt(current,'0'*64)
    for changed in [current+QUERY,current.replace(MAIN,b''),current.replace(b'\n',b'\r\n')]:
        with pytest.raises(ValueError):adapt(changed,hashlib.sha256(changed).hexdigest())


def test_unit_uses_fresh_public_data_and_unchanged_limits():
    raw=b'owned context unit fixture';paths=locations(raw);unit=render(raw)
    assert paths['data'].startswith('/var/lib/heguang-coding-public-dev32ctx42-')
    assert 'stage-dev32ctx41-' not in paths['unit']
    for line in ['User=admin','MemoryHigh=320M','MemoryMax=384M','MemorySwapMax=512M','CPUQuota=100%',
                 'TasksMax=64','NoNewPrivileges=true','ProtectSystem=strict','ProtectHome=true',
                 'ReadWritePaths='+paths['data'],'Restart=on-failure']:
        assert line in unit.splitlines()

def test_real_entry_has_budgeted_auth_and_fresh_storage_without_loading_model(tmp_path,monkeypatch):
    root,path,manifest=bundle(tmp_path);digest=hashlib.sha256(path.read_bytes()).hexdigest()
    import public_manifest,public_unit
    parent=WORK/'dev41-private-query-context-kit'
    assert hashlib.sha256((parent/'runtime-manifest.json').read_bytes()).hexdigest()==PARENT_MANIFEST_SHA
    monkeypatch.setattr(public_manifest,'PARENT',parent)
    data=tmp_path/'fresh';data.mkdir();monkeypatch.setattr(public_unit,'locations',lambda raw:{'data':str(data)})
    unused=tmp_path/'protected-original-data'
    monkeypatch.setenv('HCM_DATA_DIR',str(unused));monkeypatch.setenv('HCM_API_KEY','owned-context-key')
    monkeypatch.setenv('HCM_RERANKER_DIR',str(tmp_path/'owned-model-path'))
    monkeypatch.setenv('QIANORA_PUBLIC_MANIFEST_SHA',digest)
    monkeypatch.setattr(__import__('os'),'geteuid',lambda:1000,raising=False)
    public_manifest.parent_runtime()
    sys.path[:0]=[str(parent),str(parent/'source')]
    import manifest_validation
    monkeypatch.setattr(manifest_validation,'validate_model',lambda folder,receipt:None)
    from heguang_code import reranker
    class OwnedScorer:
        def __init__(self,*args,**kwargs):pass
        def scores(self,query,documents):return [-float(i) for i in range(len(documents))]
    monkeypatch.setattr(reranker,'PairScorer',OwnedScorer)
    spec=importlib.util.spec_from_file_location('owned_context_entry',root/'public_runner.py')
    entry=importlib.util.module_from_spec(spec);spec.loader.exec_module(entry)
    from fastapi.testclient import TestClient
    payload={'user_id':'owned','session_id':'owned','request_id':'one','messages':[
        {'role':'user','content':('def owned_lease_'+str(i)+'(key):\n    return lease_owner(key)\n')*10} for i in range(12)]}
    query={'user_id':'owned','query':'owned_lease lease_owner','top_k':100}
    with TestClient(entry.prepare()) as client:
        health=client.get('/health').json()
        assert health['version']=='0.2.0.dev32+ctx6x16k' and health['core_version']=='0.2.0.dev32'
        assert health['evidence_budget_characters']==16000 and health['max_memory_records']==6
        assert client.post('/add',json=payload).status_code==401
        assert client.post('/search',json=query).status_code==401
        headers={'Authorization':'Bearer owned-context-key'}
        assert client.post('/add',json=payload,headers=headers).status_code==200
        reply=client.post('/search',json=query,headers=headers);assert reply.status_code==200
        rows=reply.json()['data'];assert 0<len(rows)<=6 and sum(len(row['content']) for row in rows)<=16000
        assert client.post('/search',json={**query,'user_id':'other'},headers=headers).json()=={'data':[]}
    assert list(data.glob('*.sqlite3')) and not unused.exists()
