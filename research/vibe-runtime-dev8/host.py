"""Host orchestration of metadata-only image checks; no test invocation."""
import argparse,json,subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--case',required=True);a=p.parse_args()
root=Path(__file__).resolve().parent;plan=json.loads((root/'plan.json').read_text());case=next(c for c in plan['cases'] if c['case']==a.case)
subprocess.run(['docker','pull',case['image']],check=True,timeout=900)
image=json.loads(subprocess.run(['docker','image','inspect',case['image']],check=True,capture_output=True,text=True).stdout)[0]
assert image['Id']==case['image_id']
command=['docker','run','--rm','--network=none','--cap-drop=ALL','--security-opt=no-new-privileges','--cpus=2','--memory=3g','--pids-limit=128','--mount','type=bind,src='+str(root.resolve())+',dst=/kit,readonly','--workdir',case['working_dir'],'--entrypoint','python',case['image_id'],'-B','/kit/inside.py','--case',case['case']]
result=subprocess.run(command,capture_output=True,text=True,timeout=120)
print('QIANORA_VIBE_IMAGE '+json.dumps({'case':case['case'],'image_id':image['Id'],'repo_digests':image.get('RepoDigests',[])}))
print(result.stdout);print(result.stderr)
raise SystemExit(result.returncode)
