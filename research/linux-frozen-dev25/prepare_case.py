"""Container-only preparation of a fixed reviewed public proposal."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

p=argparse.ArgumentParser();p.add_argument('case');p.add_argument('arm');args=p.parse_args()
kit=Path('/kit');repo=Path('/testbed');evidence=Path('/evidence')
assert sys.platform.startswith('linux') and Path.cwd()==repo
manifest=json.loads((kit/'manifest.json').read_text())
case=next(c for c in manifest['cases'] if c['instance_id']==args.case)
assert args.arm=='baseline' or any(a['arm']==args.arm and a['status']=='frozen_reviewed' for a in case['arms'])
head=subprocess.run(['git','rev-parse','HEAD'],check=True,capture_output=True,text=True).stdout.strip()
assert head==case['base_commit'],'Image HEAD is not the frozen base commit'
folder=kit/case['instance_id']
expected={}
for arm in case['arms']:
 if arm['status']!='frozen_reviewed':continue
 for name,digest in json.loads((folder/arm['arm']/'expected-originals.json').read_text()).items():
  if name in expected:assert expected[name]==digest
  expected[name]=digest
for name,digest in expected.items():
 path=(repo/name).resolve();assert path.is_relative_to(repo)
 assert hashlib.sha256(path.read_text(encoding='utf-8').encode()).hexdigest()==digest,'Frozen original differs: '+name
patches=[folder/'test.patch']
if args.arm!='baseline':patches.append(folder/args.arm/'proposal.patch')
for patch in patches:
 subprocess.run(['git','apply','--check',str(patch)],check=True)
 subprocess.run(['git','apply',str(patch)],check=True)
(evidence/'preflight.json').write_text(json.dumps({'base_commit':head,'original_files_verified':len(expected),'arm':args.arm,'target_solution_read':False,'model_regenerated':False,'full_allowed':False},indent=2)+'\n')
