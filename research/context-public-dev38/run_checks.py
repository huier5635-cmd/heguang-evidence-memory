"""Execute only owned wiring checks after verifying all declared input bytes."""
import hashlib,json,os,pathlib,subprocess,sys,time
ROOT=pathlib.Path(__file__).resolve().parent
manifest=json.loads((ROOT/'bundle-source.json').read_text('utf-8'))
assert all(hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==h for name,h in manifest['files_sha256'].items())
start=time.monotonic()
env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1'}
result=subprocess.run([sys.executable,'-B','-m','pytest','-q','-p','no:cacheprovider',str(ROOT/'work/dev38-public-context-tests/test_public_context.py')],cwd=ROOT,env=env,capture_output=True,text=True,timeout=90)
print(result.stdout);print(result.stderr)
unchanged=all(hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==h for name,h in manifest['files_sha256'].items())
report={'complete':result.returncode==0 and '17 passed' in result.stdout and unchanged,'exit_code':result.returncode,'source_unchanged':unchanged,'checks_expected':17,'seconds':round(time.monotonic()-start,3),'bundle_source_sha256':hashlib.sha256((ROOT/'bundle-source.json').read_bytes()).hexdigest(),'owned_fixtures_only':True,'model_loaded':False,'server_or_official_endpoint_access':False,'real_cloud_or_native_repair_gain_proven':False,'full_allowed':False}
print('QIANORA_DEV38_WIRING '+json.dumps(report,sort_keys=True),flush=True)
if not report['complete']:raise SystemExit(2)
