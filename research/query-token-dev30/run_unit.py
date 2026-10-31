import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parent
suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'),pattern='test_query_anchors.py')
result=unittest.TextTestRunner(stream=sys.stdout,verbosity=1).run(suite)
report={'complete':result.wasSuccessful() and result.testsRun==13 and not result.skipped,
    'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),
    'test_source_sha256':hashlib.sha256((ROOT/'tests/test_query_anchors.py').read_bytes()).hexdigest(),
    'real_model_used':False,'benchmark_data_used':False,'full_allowed':False}
(ROOT/'unit-result.json').write_text(json.dumps(report,indent=2)+'\n','utf-8')
print('QIANORA_DEV30_UNIT '+json.dumps(report,sort_keys=True),flush=True)
if not report['complete']:raise SystemExit(2)
