"""Execute both declared capacity variants once; preserve either failure."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parent

def sha(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def cleanup_owned(kit,image):
    plan_path=kit/'capacity-result/frozen-plan.json'
    if not plan_path.is_file():return
    plan=json.loads(plan_path.read_text('utf-8'))
    assert plan['image']==image
    name='qianora-dev25-capacity-'+sha(plan_path)[:16]
    inspected=subprocess.run(['docker','inspect',name],capture_output=True,text=True,timeout=15)
    if inspected.returncode:return
    item=json.loads(inspected.stdout)[0]
    assert item['Name']=='/'+name and item['Image']==image
    mounts={row['Destination']:Path(row['Source']).resolve() for row in item['Mounts']}
    assert mounts['/kit']==kit.resolve() and mounts['/output']==(kit/'capacity-result').resolve()
    subprocess.run(['docker','stop','--time','5',name],check=True,capture_output=True,timeout=15)
    subprocess.run(['docker','rm',name],check=True,capture_output=True,timeout=15)

def run():
    plan=json.loads((ROOT/'pair-plan.json').read_text('utf-8'));image=os.environ['QIANORA_CAPACITY_IMAGE']
    assert plan['order']==['baseline','candidate'] and plan['order_declared_before_run']
    assert sha(Path(__file__))==plan['pair_runner_sha256']
    assert image.startswith('sha256:') and len(image)==71
    spec=importlib.util.spec_from_file_location('owned_allocator',ROOT/'candidate/deployment/request_lifecycle.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    marker=bytearray(b'owned-live-buffer'*50000);before=bytes(marker)
    assert sys.platform=='linux' and module.TRIM is not None
    canary=module.release_unused_heap()
    assert canary['available'] is True and canary['released'] in (0,1) and bytes(marker)==before
    del marker,before
    outcomes={}
    for variant in plan['order']:
        kit=ROOT/variant;entry=plan['variants'][variant]
        assert sha(kit/'prepared-source-manifest.json')==entry['manifest_sha256']
        assert all(sha(kit/name)==digest for name,digest in entry['files_sha256'].items())
        # Same full pinned file reads before each arm; no first-arm cold-cache claim.
        assets=json.loads((kit/'inputs/result.json').read_text('utf-8'))
        assert sha(kit/'inputs'/assets['files'][0]['local_path'])==assets['files'][0]['sha256']
        model=json.loads((kit/'model-receipt.json').read_text('utf-8'))
        assert all(sha(kit/'model'/row['path'])==row['sha256'] for row in model['files'])
        transport_failure=None
        try:
            process=subprocess.run([sys.executable,'-B',str(kit/'probe_linux.py')],timeout=480)
            exit_code=process.returncode
        except subprocess.TimeoutExpired:
            exit_code=124;transport_failure='pair_probe_wall_limit';cleanup_owned(kit,image)
        result_path=kit/'capacity-result/result.json'
        outcomes[variant]={'exit_code':exit_code,'transport_failure':transport_failure,
            'result':json.loads(result_path.read_text('utf-8')) if result_path.is_file() else None}
    baseline=outcomes['baseline']['result'];candidate=outcomes['candidate']['result']
    both=bool(baseline and candidate and all(outcomes[name]['exit_code']==0 and outcomes[name]['result']['complete'] for name in plan['order']))
    equal=False;allocator=False;benefit=False;metrics=None
    if both:
        equal=all([row['response_sha256'] for row in baseline[phase]['requests']]==[row['response_sha256'] for row in candidate[phase]['requests']] for phase in ['parallel_search','parallel_history_search'])
        records=candidate.get('allocator_records',[])
        allocator=(not baseline.get('allocator_records') and len(records)==17 and all(set(row)=={'available','released','seconds'} and row['available'] is True and row['released'] in (0,1) for row in records))
        bswap=int(baseline['actual_final_cgroup']['memory.swap.current']);cswap=int(candidate['actual_final_cgroup']['memory.swap.current'])
        bram=int(baseline['actual_final_cgroup']['memory.peak']);cram=int(candidate['actual_final_cgroup']['memory.peak'])
        faster=candidate['parallel_replay']['seconds']<=baseline['parallel_replay']['seconds']*.85
        less_swap=bswap-cswap>=64*1024**2 and cswap<=bswap*.75 and cram<=bram+16*1024**2
        not_slower=candidate['seconds']<=baseline['seconds']*1.05
        benefit=bool(equal and allocator and not_slower and (faster or less_swap))
        metrics={'baseline_seconds':baseline['seconds'],'candidate_seconds':candidate['seconds'],
            'baseline_parallel_add_seconds':baseline['parallel_replay']['seconds'],'candidate_parallel_add_seconds':candidate['parallel_replay']['seconds'],
            'baseline_final_swap_bytes':bswap,'candidate_final_swap_bytes':cswap,
            'baseline_ram_peak_bytes':bram,'candidate_ram_peak_bytes':cram,
            'total_not_slower_105_percent':not_slower,'parallel_add_at_least_15_percent_faster':faster,'swap_gate_passed':less_swap}
    result={'complete':both,'same_image':image,'pair_plan_sha256':sha(ROOT/'pair-plan.json'),
        'native_live_buffer_canary':canary,
        'all_32_search_responses_equal':equal,'actual_allocator_calls_verified':allocator,
        'prospective_capacity_benefit_gate_passed':benefit,'metrics':metrics,
        'variant_results':{name:{'exit_code':row['exit_code'],'complete':bool(row['result'] and row['result']['complete']),'result_sha256':sha(ROOT/name/'capacity-result/result.json') if (ROOT/name/'capacity-result/result.json').is_file() else None} for name,row in outcomes.items()},
        'repeatable_cloud_gain_demonstrated':False,'repair_benefit_demonstrated':False,'online_service_changed':False,'official_calls':0,'full_allowed':False}
    (ROOT/'pair-result.json').write_text(json.dumps(result,indent=2)+'\n','utf-8')
    print('QIANORA_DEV28_PAIR '+json.dumps(result,sort_keys=True),flush=True)
    if not both:raise SystemExit(2)

if __name__=='__main__':run()
