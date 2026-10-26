"""Post-oracle diagnostics only: exact original baseline, no candidate execution."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zlib
from evaluate_native_linux import KIT, execute, assessment, read, sha

output = Path(sys.argv[1])
for name, expected in read(KIT / 'bundle-hashes.json').items():
    assert sha(KIT / name) == expected, name
case = next(c for c in read(KIT / 'manifest.json')['cases'] if c['instance_id'] == 'xgcm__xgcm-558')
subprocess.run(['docker','pull',case['image']],check=True,timeout=900)
info = json.loads(subprocess.run(['docker','image','inspect',case['image']],check=True,capture_output=True,text=True).stdout)[0]
assert info['Id'] == case['image_id']
output.mkdir(parents=True,exist_ok=False)
baseline = execute(case,'baseline',output/'baseline',info['Id'])
declarations = read(KIT / case['instance_id'] / 'declared-tests.json')
report = {'case':case['instance_id'],'image_id':info['Id'],'reports':{'baseline':baseline},
          'baseline_assessment':assessment(baseline,baseline,declarations),
          'evaluation':{},'diagnostic_only':True,'official_score':False,
          'target_solution_read':False,'model_regenerated':False,'full_allowed':False}
raw=(json.dumps(report,indent=2)+'\n').encode()
(output/'result.json').write_bytes(raw)
encoded=base64.b64encode(zlib.compress(raw,9)).decode()
assert len(encoded)<4*1024*1024
print('QIANORA_RESULT_SHA256 '+hashlib.sha256(raw).hexdigest())
for offset in range(0,len(encoded),12000):
    print('QIANORA_EVIDENCE '+str(offset//12000)+' '+encoded[offset:offset+12000])
print(json.dumps({'diagnostic_only':True,'baseline_assessment':report['baseline_assessment']}))
