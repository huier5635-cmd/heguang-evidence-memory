"""Sequential fixed-image full-suite evaluation on a standard free Linux runner."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
import zlib
from full_suite_protocol import exact_outcomes, assess

KIT=Path(__file__).resolve().parent

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--case',required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
 manifest=json.loads((KIT/'manifest.json').read_text())
 case=next(c for c in manifest['cases'] if c['instance_id']==args.case)
 assert re.fullmatch(r'[a-z0-9_\-]+',case['instance_id'])
 hashes=json.loads((KIT/'bundle-hashes.json').read_text())
 assert all(sha(KIT/name)==expected for name,expected in hashes.items())
 args.output.mkdir(parents=True,exist_ok=False)
 image='starryzhang/sweb.eval.x86_64.'+case['instance_id'].lower().replace('__','_1776_')+':latest'
 subprocess.run(['docker','pull',image],check=True,timeout=900)
 info=json.loads(subprocess.run(['docker','image','inspect',image],check=True,capture_output=True,text=True).stdout)[0]
 image_id=info['Id'];assert re.fullmatch(r'sha256:[a-f0-9]{64}',image_id)
 arms=['baseline']+[a['arm'] for a in case['arms'] if a['status']=='frozen_reviewed']
 reports={}
 for arm in arms:
  folder=args.output/arm;folder.mkdir()
  command='source /opt/miniconda3/bin/activate && conda activate testbed && cd /testbed && python /kit/prepare_case.py '+case['instance_id']+' '+arm+' && python /kit/run_full_suite_evidence.py --output /evidence/run'
  create=['docker','create','--network=none','--cap-drop=ALL','--security-opt=no-new-privileges','--cpus=2','--memory=6g','--pids-limit=256','--mount','type=bind,src='+str(KIT.resolve())+',dst=/kit,readonly','--mount','type=bind,src='+str(folder.resolve())+',dst=/evidence','--entrypoint','/bin/bash',image_id,'-c',command]
  cid=subprocess.run(create,check=True,capture_output=True,text=True).stdout.strip();assert re.fullmatch(r'[a-f0-9]{64}',cid)
  started=time.monotonic();guard=None;exit_code=None
  try:
   with (folder/'execution.log').open('wb') as stream:
    try:
     completed=subprocess.run(['docker','start','-a',cid],stdout=stream,stderr=subprocess.STDOUT,timeout=900)
     exit_code=completed.returncode
    except subprocess.TimeoutExpired:guard='900-second execution limit'
  finally:
   state=json.loads(subprocess.run(['docker','inspect',cid],capture_output=True,text=True,check=True).stdout)[0]['State']
   exit_code=None if state.get('Running') else state['ExitCode']
   if state.get('OOMKilled'):guard='Container memory limit'
   subprocess.run(['docker','rm','-f',cid],check=True,capture_output=True)
  path=folder/'run/full-suite-events.json'
  evidence=json.loads(path.read_text()) if path.exists() else None
  reports[arm]={'exit_code':exit_code,'guard_reason':guard,'seconds':round(time.monotonic()-started,3),'preflight_passed':(folder/'preflight.json').exists(),'events':evidence,'log_sha256':sha(folder/'execution.log'),'log_tail':(folder/'execution.log').read_bytes()[-8000:].decode('utf-8',errors='replace')}
 declarations=json.loads((KIT/case['instance_id']/'declared-tests.json').read_text())
 baseline=reports['baseline'];evaluated={}
 for arm in arms[1:]:
  row=reports[arm]
  if not baseline['events'] or not row['events']:
   evaluated[arm]={'baseline_admissible':False,'resolved':False,'reason':'Missing full-suite evidence or failed preflight','full_allowed':False};continue
  evaluated[arm]=assess(exact_outcomes(baseline['events']['collected'],baseline['events']['events']),exact_outcomes(row['events']['collected'],row['events']['events']),declarations['fail_to_pass'],declarations['pass_to_pass'],baseline_exit=baseline['exit_code'],candidate_exit=row['exit_code'],baseline_collection_errors=baseline['events']['collection_errors'],candidate_collection_errors=row['events']['collection_errors'],guard_reason=baseline['guard_reason'] or row['guard_reason'])
 report={'case':case['instance_id'],'image_id':image_id,'repo_digests':info.get('RepoDigests',[]),'reports':reports,'evaluation':evaluated,'official_score':False,'target_solution_read':False,'model_regenerated':False,'full_allowed':False}
 path=args.output/'result.json';path.write_text(json.dumps(report,indent=2)+'\n')
 # Logs are free runner logs, not uploaded billable artifacts. Preserve the full
 # per-node evidence in bounded compressed chunks plus its checksum.
 payload=zlib.compress(path.read_bytes(),9);assert len(payload)<4*1024*1024
 encoded=base64.b64encode(payload).decode()
 print('QIANORA_RESULT_SHA256 '+sha(path))
 for off in range(0,len(encoded),12000):print('QIANORA_EVIDENCE '+str(off//12000)+' '+encoded[off:off+12000])
 print(json.dumps({'case':case['instance_id'],'evaluation':evaluated,'full_allowed':False},indent=2))
 if not baseline['preflight_passed'] or not baseline['events']:
  raise SystemExit(2)

if __name__=='__main__':main()
