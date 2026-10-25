"""Python 3.7 compatible metadata-only preflight inside a fixed public image."""
import argparse,hashlib,json,shutil,subprocess,sys
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--case',required=True);a=p.parse_args()
plan=json.loads(Path('/kit/plan.json').read_text(encoding='utf-8'))
case=next(c for c in plan['cases'] if c['case']==a.case)
repo=Path(case['working_dir']).resolve()
head=subprocess.run(['git','-C',str(repo),'rev-parse','HEAD'],capture_output=True,text=True,check=True).stdout.strip()
if head != case['base_commit']: raise RuntimeError('Repository HEAD mismatch before normalization')
from normalize_base import restore_declared_generated_files
normalization=restore_declared_generated_files(repo,case['base_commit'],case['expected_source_files'],['src/hdmf/_version.py'] if case['case']=='hdmf-dev__hdmf-839' else [], {'src/hdmf/_version.py':'/kit/hdmf-export-version.py'} if case['case']=='hdmf-dev__hdmf-839' else None)
changed=[];missing=[]
for name,expected in case['expected_source_files'].items():
 path=(repo/name).resolve()
 try:path.relative_to(repo)
 except ValueError:raise RuntimeError('Source path outside fixed repository')
 if not path.is_file():missing.append(name)
 elif hashlib.sha256(path.read_bytes()).hexdigest()!=expected:changed.append(name)
pytest_error=None;pytest_version=None
try:
 import pytest
 pytest_version=pytest.__version__
except Exception as error:pytest_error=type(error).__name__+': '+str(error)[:160]
command=shutil.which('pytest');shebang=None
if command:
 first=Path(command).read_bytes().splitlines()[0]
 if first.startswith(b'#!'):shebang=first.decode('utf-8',errors='replace')
report={'normalization':normalization,'case':case['case'],'expected_base_commit':case['base_commit'],'actual_base_commit':head,'source_files_expected':len(case['expected_source_files']),'changed_source_files':changed,'missing_source_files':missing,'python_executable':sys.executable,'python_version':sys.version,'pytest_version':pytest_version,'pytest_import_error':pytest_error,'pytest_executable':command,'pytest_shebang':shebang,'target_tests_or_solutions_loaded':False,'tests_executed':False,'generated_code_executed':False,'full_allowed':False}
print('QIANORA_VIBE_PREFLIGHT '+json.dumps(report,sort_keys=True))
if head!=case['base_commit'] or changed or missing or pytest_error:raise SystemExit(2)
