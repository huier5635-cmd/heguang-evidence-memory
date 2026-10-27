"""Exact public image and BASE tests, with private owned-container cleanup."""
import argparse
import json
from pathlib import Path, PurePosixPath
import re
import subprocess


parser=argparse.ArgumentParser()
parser.add_argument('--case',required=True)
args=parser.parse_args()
kit=Path(__file__).resolve().parent
case=next(c for c in json.loads((kit/'plan.json').read_text())['cases'] if c['case']==args.case)
subprocess.run(['docker','pull',case['image']],check=True,timeout=900)
info=json.loads(subprocess.run(['docker','image','inspect',case['image']],check=True,
    capture_output=True,text=True).stdout)[0]
if info['Id']!=case['image_id']:
    raise ValueError('Pinned image config changed')
directory=info['Config'].get('WorkingDir')
if not directory or directory=='/' or not PurePosixPath(directory).is_absolute():
    raise ValueError('A fixed repository working directory is required')
from native_environment import checked_environment
environment=checked_environment({'instance_id':case['case'],'test_environment':case.get('test_environment',{})},
    json.loads((kit/'native-test-commands.json').read_text()))
env_arguments=[value for name in sorted(environment) for value in ('--env',name+'='+environment[name])]
command=['docker','create','--network=none','--cap-drop=ALL','--security-opt=no-new-privileges',
    '--cpus=2','--memory=3g','--pids-limit=128','--mount',
    'type=bind,src='+str(kit)+',dst=/kit,readonly','--workdir',directory,
    *env_arguments,'--entrypoint','python',info['Id'],'-B','/kit/inside.py','--case',args.case]
container=subprocess.run(command,check=True,capture_output=True,text=True,timeout=90).stdout.strip()
if not re.fullmatch('[0-9a-f]{64}',container):
    raise ValueError('Unexpected owned container identity')
result=None;failure=None;state=None
try:
    result=subprocess.run(['docker','start','-a',container],capture_output=True,text=True,timeout=300)
except Exception as error:
    failure=type(error).__name__
finally:
    try:
        state=json.loads(subprocess.run(['docker','inspect',container],check=True,
            capture_output=True,text=True,timeout=20).stdout)[0]['State']
    finally:
        subprocess.run(['docker','rm','-f',container],check=True,stdout=subprocess.DEVNULL,timeout=30)
print('QIANORA_VIBE_IMAGE '+json.dumps({'case':args.case,'image_id':info['Id'],
    'working_dir':directory,'repo_digests':info.get('RepoDigests',[])}))
print('QIANORA_PREFLIGHT_CONTAINER '+json.dumps({'case':args.case,'state':state,
    'failure_type':failure,'reaped':True}))
if result is not None:
    print(result.stdout);print(result.stderr)
if failure or state is None or state['Running'] or state['OOMKilled'] or state['ExitCode']!=0:
    raise SystemExit(2)
