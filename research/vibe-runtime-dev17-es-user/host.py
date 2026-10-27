"""Fixed public image, network-disabled BASE tests, no generated code or oracles."""
import argparse
import json
from pathlib import Path, PurePosixPath
import subprocess
import re

p = argparse.ArgumentParser()
p.add_argument('--case', required=True)
a = p.parse_args()
kit = Path(__file__).resolve().parent
case = next(c for c in json.loads((kit / 'plan.json').read_text())['cases'] if c['case'] == a.case)
subprocess.run(['docker', 'pull', case['image']], check=True, timeout=900)
image = json.loads(subprocess.run(['docker', 'image', 'inspect', case['image']], check=True,
                                capture_output=True, text=True).stdout)[0]
if image['Id'] != case['image_id']:
    raise ValueError('Pinned public image config changed')
directory = image['Config'].get('WorkingDir')
if not directory or directory == '/' or not PurePosixPath(directory).is_absolute():
    raise ValueError('Expected a repository working directory')
dependency = case.get('elasticsearch_dependency')
service = None
if dependency is not None:
    assert a.case == 'elastic__curator-1437' and dependency['version'] == '6.7.1'
    subprocess.run(['docker', 'pull', dependency['image']], check=True, timeout=900)
    info = json.loads(subprocess.run(['docker', 'image', 'inspect', dependency['image']], check=True,
                                   capture_output=True, text=True).stdout)[0]
    assert info['Id'] == dependency['image_id'], 'Elasticsearch image config changed'
    service = subprocess.run(['docker', 'run', '-d', '--user=1000:1000', '--network=none', '--cap-drop=ALL',
        '--security-opt=no-new-privileges', '--cpus=1', '--memory=1g', '--pids-limit=256',
        '-e', 'ES_JAVA_OPTS=-Xms256m -Xmx256m', '-e', 'discovery.type=single-node',
        '-e', 'cluster.name=qianora-dev17-base', '-e', 'network.host=127.0.0.1',
        '-e', 'http.port=9200', '-e', 'xpack.security.enabled=false',
        '-e', 'xpack.ml.enabled=false', dependency['image']],
        check=True, capture_output=True, text=True, timeout=90).stdout.strip()
    assert re.fullmatch('[0-9a-f]{64}', service), 'Unexpected isolated container identity'
network = '--network=none' if service is None else '--network=container:' + service
command = ['docker', 'run', '--rm', network, '--cap-drop=ALL', '--security-opt=no-new-privileges',
           '--cpus=2', '--memory=3g', '--pids-limit=128', '--mount',
           'type=bind,src=' + str(kit.resolve()) + ',dst=/kit,readonly', '--workdir', directory,
           '--entrypoint', 'python', image['Id'], '-B', '/kit/inside.py', '--case', a.case]
result = None
try:
    result = subprocess.run(command, capture_output=True, text=True, timeout=300)
finally:
    if service is not None:
        # Only this newly-created isolated container, with no host ports or
        # external network. Always remove it, including on failed base tests.
        try:
            state = json.loads(subprocess.run(['docker', 'inspect', service], check=True,
                                             capture_output=True, text=True).stdout)[0]
            print('QIANORA_ELASTICSEARCH_DEPENDENCY ' + json.dumps({
                'image_id': state['Image'], 'container_user': state['Config']['User'], 'network_mode': state['HostConfig']['NetworkMode'],
                'memory_bytes': state['HostConfig']['Memory'], 'port_bindings': state['HostConfig'].get('PortBindings'),
                'oom_killed': state['State']['OOMKilled'], 'running': state['State']['Running']}))
            if result is None or result.returncode:
                logs = subprocess.run(['docker', 'logs', '--tail', '60', service], capture_output=True, text=True, timeout=20)
                print('QIANORA_ELASTICSEARCH_STARTUP_DIAGNOSTIC ' + (logs.stdout + logs.stderr)[:12000])
        finally:
            subprocess.run(['docker', 'rm', '-f', service], check=True, stdout=subprocess.DEVNULL, timeout=30)
print('QIANORA_VIBE_IMAGE ' + json.dumps({'case': a.case, 'image_id': image['Id'], 'working_dir': directory,
                                       'repo_digests': image.get('RepoDigests', [])}))
print(result.stdout)
print(result.stderr)
raise SystemExit(result.returncode)
