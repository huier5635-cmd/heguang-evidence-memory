"""Fixed public image, network-disabled BASE tests, no generated code or oracles."""
import argparse
import json
from pathlib import Path, PurePosixPath
import subprocess

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
command = ['docker', 'run', '--rm', '--network=none', '--cap-drop=ALL', '--security-opt=no-new-privileges',
           '--cpus=2', '--memory=3g', '--pids-limit=128', '--mount',
           'type=bind,src=' + str(kit.resolve()) + ',dst=/kit,readonly', '--workdir', directory,
           '--entrypoint', 'python', image['Id'], '-B', '/kit/inside.py', '--case', a.case]
result = subprocess.run(command, capture_output=True, text=True, timeout=300)
print('QIANORA_VIBE_IMAGE ' + json.dumps({'case': a.case, 'image_id': image['Id'], 'working_dir': directory,
                                       'repo_digests': image.get('RepoDigests', [])}))
print(result.stdout)
print(result.stderr)
raise SystemExit(result.returncode)
