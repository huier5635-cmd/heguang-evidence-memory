"""One real-MiniLM baseline/candidate comparison on frozen owned inputs only."""
import hashlib
import importlib
import importlib.metadata
import importlib.util
import json
import math
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parent

def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()
def read(path):return json.loads(Path(path).read_text('utf-8'))
def package(variant):
    folder=ROOT/variant/'heguang_code';name='qianora_owned_'+variant
    spec=importlib.util.spec_from_file_location(name,folder/'__init__.py',submodule_search_locations=[str(folder)])
    module=importlib.util.module_from_spec(spec);sys.modules[name]=module;spec.loader.exec_module(module)
    return importlib.import_module(name+'.reranker')

class CountedSession:
    def __init__(self,session,sep):self.session=session;self.sep=sep;self.records=[]
    def run(self,names,feeds):
        ids=feeds['input_ids'];assert 1<=ids.shape[0]<=3 and ids.shape[1]<=512
        first=ids[0].tolist();end=first.index(self.sep,1);query=first[1:end]
        assert len(query)<=192
        self.records.append({'pairs':int(ids.shape[0]),'tokens':int(ids.shape[1]),'query_ids':query})
        return self.session.run(names,feeds)

def contains_sequence(haystack,needle):
    return bool(needle) and any(haystack[start:start+len(needle)]==needle for start in range(len(haystack)-len(needle)+1))

def main():
    source=read(ROOT/'bundle-source.json')
    assert all(sha(ROOT/name)==digest for name,digest in source['files_sha256'].items())
    plan=read(ROOT/'probe-plan.json');fixtures=read(ROOT/'owned-fixtures.json');unit=read(ROOT/'unit-result.json')
    assert unit['complete'] and unit['tests']==13 and unit['skipped']==0
    assert sha(ROOT/'owned-fixtures.json')==plan['fixtures_sha256'] and fixtures['self_authored'] and not fixtures['benchmark_data_used']
    assert sha(ROOT/'model-receipt.json')==plan['model_receipt_sha256']
    assert len(fixtures['cases'])==fixtures['case_count']==12
    for variant,key in [('baseline','baseline20_sha256'),('candidate','candidate21_sha256')]:
        assert all(sha(ROOT/variant/'heguang_code'/name)==digest for name,digest in plan[key].items())
    receipt=read(ROOT/'model-receipt.json')
    assert all(sha(ROOT/'model'/row['path'])==row['sha256'] for row in receipt['files'])
    models={variant:package(variant).PairScorer(ROOT/'model',threads=1) for variant in ['baseline','candidate']}
    for model in models.values():model.session=CountedSession(model.session,model.sep)
    rows=[];started=time.monotonic();failure=None
    try:
        for index,case in enumerate(fixtures['cases']):
            texts=[row['text'] for row in case['documents']];measurements={}
            order=['baseline','candidate'] if index%2==0 else ['candidate','baseline']
            for variant in order:
                model=models[variant];model.session.records.clear();before=time.monotonic()
                scores=model.scores(case['query'],texts);elapsed=time.monotonic()-before
                assert len(scores)==2 and all(math.isfinite(value) for value in scores)
                calls=list(model.session.records)
                assert len(calls)==2 and all(row['query_ids']==calls[0]['query_ids'] for row in calls)
                selected=calls[0]['query_ids'];anchor=case['middle_anchor'];preserved=None
                if anchor:
                    encoding=model.tokenizer.encode(case['query'][:20000],add_special_tokens=False)
                    start=case['query'].index(anchor);end=start+len(anchor)
                    expected=[token for token,(left,right) in zip(encoding.ids,encoding.offsets,strict=True) if left<end and right>start]
                    preserved=contains_sequence(selected,expected)
                predicted=case['documents'][max(range(2),key=lambda i:scores[i])]['id']
                measurements[variant]={'scores':scores,'seconds':round(elapsed,6),
                    'predicted_document':predicted,'correct':predicted==case['expected_document'],
                    'query_tokens':len(selected),'query_ids_sha256':hashlib.sha256(json.dumps(selected).encode()).hexdigest(),
                    'middle_anchor_preserved_in_actual_model_input':preserved,
                    'onnx_calls':len(calls),'inference_pairs':sum(row['pairs'] for row in calls),
                    'sequence_tokens_maximum':max(row['tokens'] for row in calls)}
            rows.append({'id':case['id'],'scope':case['scope'],'query_sha256':hashlib.sha256(case['query'].encode()).hexdigest(),
                'query_characters':len(case['query']),'order':order,'variants':measurements})
    except Exception as error:failure={'type':type(error).__name__,'message':str(error)[:200]}
    unchanged=[row for row in rows if row['scope']!='long_middle_technical_anchor']
    changed=[row for row in rows if row['scope']=='long_middle_technical_anchor']
    same=bool(len(unchanged)==6 and all(row['variants']['baseline']['scores']==row['variants']['candidate']['scores']
        and row['variants']['baseline']['query_ids_sha256']==row['variants']['candidate']['query_ids_sha256'] for row in unchanged))
    preserved=bool(len(changed)==6 and all(row['variants']['candidate']['middle_anchor_preserved_in_actual_model_input'] for row in changed))
    wins=[row['id'] for row in changed if row['variants']['candidate']['correct'] and not row['variants']['baseline']['correct']]
    losses=[row['id'] for row in rows if row['variants']['baseline']['correct'] and not row['variants']['candidate']['correct']]
    source_unchanged=all(sha(ROOT/name)==digest for name,digest in source['files_sha256'].items())
    complete=bool(failure is None and len(rows)==12 and same and preserved and source_unchanged)
    result={'complete':complete,'failure':failure,'probe_plan_sha256':sha(ROOT/'probe-plan.json'),
        'source_manifest_sha256':sha(ROOT/'bundle-source.json'),'owned_unit_tests':13,'owned_cases':len(rows),
        'all_six_unchanged_inputs_and_scores_identical':same,'all_six_middle_anchors_reach_real_model':preserved,
        'changed_case_wins':wins,'previously_correct_cases_lost':losses,
        'prospective_owned_relevance_gate_passed':bool(complete and len(wins)>=2 and not losses),
        'correct_counts':{variant:sum(row['variants'][variant]['correct'] for row in rows) for variant in models},
        'model_asset_sha256':{row['path']:row['sha256'] for row in receipt['files']},
        'library_versions':{name:importlib.metadata.version(name) for name in ['tokenizers','onnxruntime','numpy']},
        'total_probe_seconds':round(time.monotonic()-started,3),'cases':rows,'sources_unchanged':source_unchanged,
        'self_authored_relevance_only':True,'benchmark_data_used':False,'real_coding_repair_gain_proven':False,
        'cloud_capacity_proven':False,'online_service_changed':False,'official_calls':0,'full_allowed':False}
    (ROOT/'probe-result.json').write_text(json.dumps(result,indent=2)+'\n','utf-8')
    print('QIANORA_DEV30_OWNED_PROBE '+json.dumps(result,sort_keys=True),flush=True)
    if not complete:raise SystemExit(2)

if __name__=='__main__':main()
