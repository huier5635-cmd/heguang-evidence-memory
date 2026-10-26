"""Supplemental post-oracle environment repair; frozen proposals never regenerate."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import urllib.request
import zlib
from pip._vendor.packaging.version import Version
from evaluate_native_linux import KIT, execute, assessment, read, sha

output=Path(sys.argv[1]);output.mkdir(parents=True,exist_ok=False)
for name,expected in read(KIT/'bundle-hashes.json').items():
    assert sha(KIT/name)==expected,name
case=next(c for c in read(KIT/'manifest.json')['cases'] if c['instance_id']=='xgcm__xgcm-558')
wheel=read(KIT/'scipy-wheel.json')
subprocess.run(['docker','pull',case['image']],check=True,timeout=900)
def inspect(image):
    return json.loads(subprocess.run(['docker','image','inspect',image],check=True,capture_output=True,text=True).stdout)[0]
original=inspect(case['image']);assert original['Id']==case['image_id']
version_code="import json,importlib.metadata as m;print(json.dumps({d.metadata['Name'].lower():d.version for d in m.distributions()}))"
def versions(image):
    return json.loads(subprocess.run(['docker','run','--rm','--network=none','--cap-drop=ALL','--security-opt=no-new-privileges','--entrypoint','python',image,'-c',version_code],check=True,capture_output=True,text=True).stdout)
before=versions(original['Id'])
assert 'scipy' not in before,'Unexpected existing SciPy; investigate rather than overwrite'
assert Version('1.23.5')<=Version(before['numpy'])<Version('2.5'),'Pinned SciPy requirement incompatible; do not modify NumPy'
build=output/'build';build.mkdir()
download=build/wheel['filename']
with urllib.request.urlopen(wheel['url'],timeout=60) as source,download.open('xb') as target:
    remaining=wheel['size']
    while remaining:
        chunk=source.read(min(1024*1024,remaining))
        if not chunk:raise ValueError('Incomplete wheel')
        target.write(chunk);remaining-=len(chunk)
    assert not source.read(1),'Oversized wheel'
assert sha(download)==wheel['sha256']
dockerfile='FROM '+case['image']+'\nCOPY '+wheel['filename']+' /tmp/qianora-wheel/\nRUN python -m pip install --no-index --no-deps --no-compile /tmp/qianora-wheel/'+wheel['filename']+' && rm -rf /tmp/qianora-wheel\n'
(build/'Dockerfile').write_text(dockerfile)
subprocess.run(['docker','build','--network=none','--tag','qianora-xgcm-scipy-repair',str(build)],check=True,timeout=600)
derived=inspect('qianora-xgcm-scipy-repair');after=versions(derived['Id'])
assert after.get('scipy')==wheel['version']
assert {k:v for k,v in after.items() if k!='scipy'}==before,'Other distribution versions changed'
declarations=read(KIT/case['instance_id']/'declared-tests.json')
reports={'baseline':execute(case,'baseline',output/'baseline',derived['Id'])}
baseline=assessment(reports['baseline'],reports['baseline'],declarations)
evaluated={}
for arm in case['arms']:
    name=arm['arm']
    if arm['status']!='frozen_reviewed' or not baseline['baseline_admissible']:
        evaluated[name]={'resolved':False,'proposal_executed':False,'reason':arm['status'] if arm['status']!='frozen_reviewed' else 'Baseline inadmissible'}
    else:
        reports[name]=execute(case,name,output/name,derived['Id'])
        evaluated[name]=assessment(reports['baseline'],reports[name],declarations)
report={'case':case['instance_id'],'image_id':derived['Id'],'original_image_id':original['Id'],
        'dependency_repair':{'wheel':wheel,'versions_before':before,'versions_after':after},
        'reports':reports,'baseline_assessment':baseline,'evaluation':evaluated,
        'supplemental_post_oracle_environment_change':True,'original_failure_retained':True,
        'official_score':False,'target_solution_read':False,'model_regenerated':False,'full_allowed':False}
raw=(json.dumps(report,indent=2)+'\n').encode();(output/'result.json').write_bytes(raw)
encoded=base64.b64encode(zlib.compress(raw,9)).decode();assert len(encoded)<4*1024*1024
print('QIANORA_RESULT_SHA256 '+hashlib.sha256(raw).hexdigest())
for offset in range(0,len(encoded),12000):print('QIANORA_EVIDENCE '+str(offset//12000)+' '+encoded[offset:offset+12000])
print(json.dumps({'baseline_assessment':baseline,'evaluation':evaluated,'full_allowed':False}))
if not baseline['baseline_admissible']:raise SystemExit(2)
