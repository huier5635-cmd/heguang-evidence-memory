"""Actual frozen Add/Search, full source audits, variant-owned MiniLM, complete transport."""
import base64
import gc
import hashlib
import importlib
import importlib.util
import json
import math
from pathlib import Path
import sqlite3
import sys
import time
import urllib.request
import zlib

from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parent

def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def raw_json(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
def digest(value):return hashlib.sha256(raw_json(value)).hexdigest()
def read(path):return json.loads(Path(path).read_text('utf-8'))
def save(path,value):
    with Path(path).open('x',encoding='utf-8',newline='\n') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2);stream.write('\n')

def load(version):
    folder=ROOT/version/'heguang_code';name='qianora_dev33_'+version
    spec=importlib.util.spec_from_file_location(name,folder/'__init__.py',submodule_search_locations=[str(folder)])
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
    return {part:importlib.import_module(name+'.'+part) for part in ['api','config','schema','normalize','reranker','selection','store']}

def download_history(row,destination):
    assert row['url'].startswith('https://huggingface.co/datasets/Tongyi-ConvAI/VibeMemBench/resolve/11164976b4f920cdb0d0cd570ca572e49acea1e8/history/python/')
    for attempt in range(3):
        try:
            with urllib.request.urlopen(row['url'],timeout=90) as response:
                body=response.read(row['bytes']+1)
            assert len(body)==row['bytes'] and hashlib.sha256(body).hexdigest()==row['sha256']
            with destination.open('xb') as stream:stream.write(body)
            return
        except Exception:
            if attempt==2:raise
            time.sleep(attempt+1)

class ModelCalls:
    """Observe the real frozen scorer; no shared or cross-variant score cache."""
    def __init__(self,scorer):self.scorer=scorer;self.calls=[];self.active=None
    def scores(self,query,documents):
        assert self.active is None and 0<len(documents)<=16
        call={'query_sha256':hashlib.sha256(query.encode()).hexdigest(),'query_characters':len(query),
            'document_sha256':[hashlib.sha256(x.encode()).hexdigest() for x in documents],
            'documents':len(documents),'model_calls':[]}
        self.active=call;session=self.scorer.session;owner=self
        class Recorder:
            def run(self,outputs,feeds):
                ids=feeds['input_ids'];segments=feeds['token_type_ids']
                query_ids=[int(value) for value,segment in zip(ids[0],segments[0]) if int(segment)==0][1:-1]
                assert 0<=len(query_ids)<=192 and ids.shape[0]<=3 and ids.shape[1]<=512
                call['model_calls'].append({'query_ids_sha256':digest(query_ids),'query_tokens':len(query_ids),
                    'windows':int(ids.shape[0]),'sequence_tokens':int(ids.shape[1]),
                    'input_ids_sha256':hashlib.sha256(ids.tobytes()).hexdigest()})
                return session.run(outputs,feeds)
        self.scorer.session=Recorder()
        try:
            result=self.scorer.scores(query,documents)
            assert len(result)==len(documents) and all(math.isfinite(x) for x in result)
            call['scores']=result;assert len(call['model_calls'])==len(documents)
            self.calls.append(call);return result
        finally:self.scorer.session=session;self.active=None

def audit(store,modules,user,history_rows):
    path=store.path(user)
    with store.connect(user,create=False) as db:db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    before=sha(path);sources=raw_count=writes=0;aggregate=hashlib.sha256()
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro&immutable=1',uri=True) as db:
        db.row_factory=sqlite3.Row
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
        assert db.execute("SELECT value FROM meta WHERE key='signature'").fetchone()[0]==store.signature
        for row in history_rows:
            original=read(ROOT/'histories'/row['local_name'])
            request=modules['schema'].AddRequest(user_id=user,session_id=row['session_id'],request_id=row['path'],messages=original['messages'])
            assert db.execute('SELECT digest FROM writes WHERE request_id=?',(row['path'],)).fetchone()[0]==digest(request.model_dump())
            writes+=1
            for position,(message,view) in enumerate(zip(request.messages,modules['normalize'].trajectory_views(request.messages),strict=True)):
                sid=digest([user,request.request_id,position])[:40]
                record=db.execute('SELECT * FROM sources WHERE id=?',(sid,)).fetchone()
                text,representation,parent=view
                assert record and (record['role'],record['timestamp'],record['chars'],record['representation'])==(message.role,message.timestamp,len(text),representation)
                assert (record['request_id'],record['session_id'])==(request.request_id,request.session_id)
                assert store.source_span(db,sid,0,len(text),{})==text
                stored=db.execute('SELECT body,digest FROM raw_messages WHERE source_id=?',(sid,)).fetchone()
                expected=message.model_dump(exclude_unset=True)
                if set(expected)-{'role','content','timestamp'}:
                    assert stored is not None
                    original_bytes=zlib.decompress(stored['body'])
                    assert hashlib.sha256(original_bytes).hexdigest()==stored['digest'] and json.loads(original_bytes)==expected
                    raw_count+=1
                else:assert stored is None
                aggregate.update(raw_json({'source_id':sid,'source_sha256':hashlib.sha256(text.encode()).hexdigest(),'message_sha256':digest(expected)}))
                sources+=1
        counts={table:db.execute('SELECT count(*) FROM '+table).fetchone()[0] for table in ['sources','raw_messages','writes','docs','blocks','search','episode_docs','episode_search']}
        assert (counts['sources'],counts['raw_messages'],counts['writes'])==(sources,raw_count,writes)
        assert counts['docs']==counts['search'] and counts['episode_docs']==counts['episode_search']
    assert sha(path)==before
    return {'database_sha256':before,'database_bytes':path.stat().st_size,'signature':store.signature,
        'all_source_views_reconstructed':True,'all_extended_messages_decompressed_compared':True,
        'sources':sources,'raw_messages':raw_count,'writes':writes,'counts':counts,
        'ordered_full_source_audit_sha256':aggregate.hexdigest(),'read_only_and_unchanged':True}

def transport(report):
    raw=raw_json(report);assert len(raw)<=4*1024**2
    compressed=zlib.compress(raw,9);encoded=base64.b64encode(compressed).decode()
    print('QIANORA_DEV33_RETRIEVAL_BINDING '+json.dumps({'raw_bytes':len(raw),'raw_sha256':hashlib.sha256(raw).hexdigest(),
        'compressed_bytes':len(compressed),'compressed_sha256':hashlib.sha256(compressed).hexdigest(),
        'chunks':(len(encoded)+7999)//8000}),flush=True)
    for offset in range(0,len(encoded),8000):print('QIANORA_DEV33_RETRIEVAL_DATA '+str(offset//8000)+' '+encoded[offset:offset+8000],flush=True)

def main():
    started=time.monotonic();manifest=read(ROOT/'bundle-source.json')
    assert all(sha(ROOT/name)==h for name,h in manifest['files_sha256'].items())
    fixture=read(ROOT/'fixture.json');plan=read(ROOT/'retrieval-plan.json')
    assert len(fixture['targets'])==3 and len(fixture['history_files'])==12
    assert plan['versions']==['dev25','dev32'] and plan['score_cache_used'] is False
    data=ROOT/'histories';data.mkdir(exist_ok=False)
    for row in fixture['history_files']:download_history(row,data/row['local_name'])
    original_hashes={str(data/row['local_name']):row['sha256'] for row in fixture['history_files']}
    models=read(ROOT/'model-receipt.json')
    assert all(sha(ROOT/'model'/row['path'])==row['sha256'] for row in models['files'])
    cases={row['instance_id']:{'case':row['instance_id'],'variants':{}} for row in fixture['targets']}
    audits=[];failure=None
    try:
        for version in plan['versions']:
            modules=load(version)
            scorer=ModelCalls(modules['reranker'].PairScorer(ROOT/'model',threads=1))
            settings=modules['config'].Settings(data_dir=ROOT/'indices'/version,api_key='public-history-only',
                source_selection='coalesce',reranker_threads=1,max_concurrency=1,max_body_bytes=128*1024**2)
            app=modules['api'].create_app(settings,scorer=scorer)
            # No lifespan cleanup: timestamps in historical evidence are intentionally old.
            client=TestClient(app,headers={'X-Api-Key':'public-history-only'})
            try:
                for target in fixture['targets']:
                    user='complete-history:'+target['instance_id']
                    histories=[row for row in fixture['history_files'] if row['target_id']==target['instance_id']]
                    for row in histories:
                        raw=read(data/row['local_name'])
                        assert len(raw['messages'])==row['messages']
                        response=client.post('/add',json={'user_id':user,'session_id':row['session_id'],'request_id':row['path'],'messages':raw['messages']})
                        assert response.status_code==200 and response.json()['success'] is True
                    audited=audit(app.state.store,modules,user,histories)
                    audits.append({'case':target['instance_id'],'version':version,**audited})
                    before_calls=len(scorer.calls)
                    response=client.post('/search',json={'user_id':user,'query':target['problem_statement'],'top_k':100})
                    assert response.status_code==200
                    rows=response.json()['data'];memory=modules['reranker'].fit_budget(rows,top_k=6,characters=16000)
                    with app.state.store.connect(user,create=False) as db:
                        for row in rows:
                            meta,body=modules['selection'].source_record(row)
                            assert app.state.store.source_span(db,meta['source_id'],*meta['characters'],{})==body
                    assert sha(app.state.store.path(user))==audited['database_sha256']
                    calls=scorer.calls[before_calls:];assert len(calls)==1
                    cases[target['instance_id']]['variants'][version]={'actual_add_api_calls':len(histories),'actual_search_api_calls':1,
                        'http_status':200,'scorer_variant':version,'scorer_calls':calls,
                        'response_rows':len(rows),'response_characters':sum(len(row['content']) for row in rows),
                        'response_sha256':digest(rows),'every_response_span_verified_against_source':True,
                        'memory_rows':len(memory),'memory_characters':sum(len(row['content']) for row in memory),'memory':memory,
                        'memory_sha256':digest(memory),'database_unchanged_after_search':True}
                    print(json.dumps({'indexed_and_searched':target['instance_id'],'variant':version,
                        'files':len(histories),'messages':audited['sources'],'memory_characters':sum(len(row['content']) for row in memory)}),flush=True)
            finally:client.close()
            del scorer,app,client;gc.collect()
    except Exception as error:failure={'type':type(error).__name__,'message':str(error)[:300]}
    unchanged=all(sha(Path(p))==h for p,h in original_hashes.items()) and all(sha(ROOT/name)==h for name,h in manifest['files_sha256'].items())
    report={'complete':failure is None and len(audits)==6 and unchanged,'failure':failure,
        'cases':list(cases.values()),'full_source_audits':audits,'source_and_history_unchanged':unchanged,
        'bundle_source_sha256':sha(ROOT/'bundle-source.json'),'retrieval_plan_sha256':sha(ROOT/'retrieval-plan.json'),
        'fixture_sha256':sha(ROOT/'fixture.json'),'real_variant_specific_pair_scorers':True,'score_cache_used':False,
        'all6_actual_searches_and24_actual_adds':failure is None and len(audits)==6,
        'elapsed_seconds':round(time.monotonic()-started,3),'target_tests_or_gold_read':False,
        'generation_started':False,'coding_repair_gain_proven':False,'official_calls':0,'paid_api_calls':0,'full_allowed':False}
    save(ROOT/'retrieval-result.json',report);transport(report)
    print(json.dumps({key:value for key,value in report.items() if key not in ['cases','full_source_audits']}),flush=True)
    if not report['complete']:raise SystemExit(2)

if __name__=='__main__':main()
