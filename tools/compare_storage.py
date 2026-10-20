"""Compare an isolated storage candidate against fixed v0.1.1 on public data.

No private evaluation inputs, real model calls, official runs, or running-service edits.
Only aggregate counts, digests, disk sizes and timings are saved as results.
"""
import argparse
import asyncio
import hashlib
import importlib
import importlib.util
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT/'work/fts-validation'
BASE = RUN/'baseline'
NEW = RUN/'candidate'

def copy_sources(source, target):
    target.mkdir(parents=True,exist_ok=True)
    for folder in ('aml_memory','tests','tools'):
        (target/folder).mkdir(exist_ok=True)
        for path in (source/folder).glob('*.py'):
            shutil.copyfile(path,target/folder/path.name)
    shutil.copyfile(source/'pyproject.toml',target/'pyproject.toml')

def package(alias, folder):
    spec=importlib.util.spec_from_file_location(alias,folder/'aml_memory/__init__.py',submodule_search_locations=[str(folder/'aml_memory')])
    module=importlib.util.module_from_spec(spec)
    sys.modules[alias]=module
    spec.loader.exec_module(module)
    return {name:importlib.import_module(alias+'.'+name) for name in ('config','engine','schema')}

def load_eval():
    spec=importlib.util.spec_from_file_location('fts_public_eval',BASE/'tools/public_eval.py')
    module=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=module
    spec.loader.exec_module(module)
    return module

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

async def check_lifecycle(packages):
    checks=[]
    states=[]
    for alias,modules in packages:
        settings=modules['config'].Settings(data_dir=RUN/'lifecycle'/alias,api_key='local-only-test')
        engine=modules['engine'].Engine(settings)
        payloads=[{'request_id':'old','user_id':'lifecycle','session_id':'s','messages':[{'role':'user','content':'Expired silver comet notebook.'}]},
                  {'request_id':'fresh','user_id':'lifecycle','session_id':'s','messages':[{'role':'user','content':'Fresh copper astronomy notebook.'}]}]
        for value in payloads:
            await engine.add(modules['schema'].AddRequest(**value))
        db=engine.store.connect('lifecycle')
        for table in ('docs','messages','writes'):
            db.execute(f'UPDATE {table} SET added_at=0 WHERE request_id=?',('old',))
        db.commit(); db.close()
        engine.store.purge_expired(29)
        await engine.models.close()
        # A new Engine tests reopen/persistence, independently of Python object caches.
        engine=modules['engine'].Engine(settings)
        req=modules['schema'].SearchRequest(user_id='lifecycle',query='copper astronomy',top_k=100)
        after=await engine.search(req)
        assert len(after)==1 and 'Fresh copper' in after[0]['content']
        assert 'silver comet' not in json.dumps(after)
        await engine.add(modules['schema'].AddRequest(**payloads[1]))
        assert await engine.search(req)==after
        fresh2={'request_id':'new-after-purge','user_id':'lifecycle','session_id':'s','messages':[{'role':'user','content':'New violet eclipse journal.'}]}
        await engine.add(modules['schema'].AddRequest(**fresh2))
        violet=await engine.search(modules['schema'].SearchRequest(user_id='lifecycle',query='violet eclipse',top_k=100))
        assert any('New violet' in item['content'] for item in violet)
        # Fail part-way through the transaction, after some ordinary rows and an FTS insert.
        original=engine.store.connect
        def deny_trigrams(user_id,create=True):
            connection=original(user_id,create)
            if connection:
                connection.set_authorizer(lambda action,table,*rest: sqlite3.SQLITE_DENY if action==sqlite3.SQLITE_INSERT and table=='trigrams' else sqlite3.SQLITE_OK)
            return connection
        engine.store.connect=deny_trigrams
        failed={'request_id':'failed-write','user_id':'lifecycle','session_id':'s','messages':[{'role':'user','content':'Rollbackneedle must never appear.'}]}
        try:
            await engine.add(modules['schema'].AddRequest(**failed))
        except sqlite3.DatabaseError:
            pass
        else:
            raise AssertionError('Failure injection did not interrupt transaction')
        engine.store.connect=original
        db=engine.store.connect('lifecycle')
        for table in ('docs','messages','writes'):
            assert db.execute(f'SELECT count(*) FROM {table} WHERE request_id=?',('failed-write',)).fetchone()[0]==0
        assert not engine.store.lexical(db,'rollbackneedle','words',100)
        db.close()
        states.append(await engine.search(modules['schema'].SearchRequest(user_id='lifecycle',query='notebook eclipse',top_k=100)))
        await engine.models.close()
    assert states[0]==states[1]
    checks=['mixed_age_purge','remaining_evidence','reopen_persistence','retry_after_reopen','add_after_purge','partial_fts_failure_rollback','cross_version_array_equivalence']
    return checks

async def compare_dataset(evaluator,packages,path,label):
    samples=evaluator.load_dataset(path)
    engines=[(alias,modules,modules['engine'].Engine(modules['config'].Settings(data_dir=RUN/'data'/label/alias,api_key='local-only-test'))) for alias,modules in packages]
    started=time.monotonic()
    count=adds=messages=0
    for sample_index,sample in enumerate(samples):
        user='public-fts-regression:'+str(sample['sample_id'])
        for session,date,turns in evaluator.turns_by_session(sample):
            for index,chunk in enumerate(evaluator.chunk_turns(turns)):
                payload={'request_id':f'{user}:{session}:{index}','user_id':user,'session_id':f'{user}:{session}',
                         'messages':[{'role':'user' if turn.speaker==sample.get('speaker_a') else 'assistant',
                                      'content':f'[{turn.evidence_id}] {turn.speaker}: {turn.text} (session date: {date})'} for turn in chunk]}
                for alias,modules,engine in engines:
                    await engine.add(modules['schema'].AddRequest(**payload))
                adds+=1; messages+=len(chunk)
        for question in evaluator.valid_questions(sample):
            responses=[]
            for alias,modules,engine in engines:
                responses.append(await engine.search(modules['schema'].SearchRequest(user_id=user,query=question['question'],top_k=100)))
            if responses[0]!=responses[1]:
                raise AssertionError(f'Exact Search array mismatch in {label}, aggregate query position {count}')
            count+=1
            if count%100==0:
                print(json.dumps({'dataset':label,'exact_arrays':count,'elapsed_s':round(time.monotonic()-started,1)}),flush=True)
        print(json.dumps({'dataset':label,'sample_finished':sample_index+1,'queries_compared':count,'add_requests':adds}),flush=True)
    sizes={}
    docs={}
    for alias,modules,engine in engines:
        await engine.models.close()
        files=list(engine.store.root.glob('*.sqlite3'))
        docs[alias]=0
        for file in files:
            db=sqlite3.connect(file)
            docs[alias]+=db.execute('SELECT count(*) FROM docs').fetchone()[0]
            assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            db.close()
        sizes[alias]=sum(file.stat().st_size for file in engine.store.root.glob('*.sqlite3*'))
    return {'dataset':label,'dataset_sha256':digest(path),'samples':len(samples),'add_requests':adds,'messages':messages,
            'search_queries':count,'top_k':100,'exact_array_matches':count,'database_integrity':'ok','documents':docs,
            'database_bytes_including_wal':sizes,'elapsed_seconds':round(time.monotonic()-started,3)}

async def main(args):
    assert not (RUN/'data').exists() and not (RUN/'lifecycle').exists(), 'Use a fresh run directory; do not overwrite previous measured data'
    copy_sources(args.baseline,BASE)
    copy_sources(args.candidate,NEW)
    packages=[('baseline',package('fts_baseline',BASE)),('candidate',package('fts_candidate',NEW))]
    evaluator=load_eval()
    report={'purpose':'isolated contentless FTS storage candidate validation','baseline_label':args.baseline_label,
            'sqlite_version':sqlite3.sqlite_version,'production_changed':False,'real_model_calls':0,'official_runs':0,
            'source_sha256':{alias:{str(file.relative_to(folder)):digest(file) for file in (folder/'aml_memory').glob('*.py')} for alias,folder in [('baseline',BASE),('candidate',NEW)]}}
    report['lifecycle_checks']=await check_lifecycle(packages)
    report['public_regression']=[]
    for dataset_index,path in enumerate(args.dataset):
        label=f'{dataset_index+1}-{path.stem}'
        report['public_regression'].append(await compare_dataset(evaluator,packages,path,label))
    report['limitations']='Related public LoCoMo datasets, lexical mode, one local development machine. Exact equivalence on these inputs does not establish held-out answer quality, real model performance, crash recovery or Full capacity.'
    output=args.output
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':'pass','output':output.name,'public_queries':sum(r['search_queries'] for r in report['public_regression'])}),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline',type=Path,required=True)
    parser.add_argument('--candidate',type=Path,default=ROOT)
    parser.add_argument('--dataset',type=Path,action='append',required=True,help='Public LoCoMo-format JSON; may repeat. Never supply private official evaluation data.')
    parser.add_argument('--work-dir',type=Path,required=True,help='Fresh output directory. Existing measured data is never removed.')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--baseline-label',default='user-provided baseline directory')
    args=parser.parse_args()
    RUN=args.work_dir.resolve()
    BASE=RUN/'baseline'
    NEW=RUN/'candidate'
    if args.baseline.resolve() in (BASE,NEW) or args.candidate.resolve() in (BASE,NEW):
        parser.error('Source directories must be outside the fresh work directory')
    asyncio.run(main(args))
