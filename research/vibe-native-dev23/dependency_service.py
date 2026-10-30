"""Pinned private Elasticsearch fixture, identical for every Curator arm."""
from contextlib import contextmanager
import json
from pathlib import Path
import re
import subprocess
import time
import urllib.request

IMAGE = 'docker.elastic.co/elasticsearch/elasticsearch@sha256:4b945a65ffef04f317bba80dadb6557c3aa3a124e5b6c1ec65b53dde77846527'
IMAGE_ID = 'sha256:e2667f5db2899d035b34deffd39505d489aae89ba01a386eeea40cb2072a8a3a'


def configured(case):
    dependency = case.get('elasticsearch_dependency')
    if dependency is None:
        if case['instance_id'] == 'elastic__curator-1437':
            raise ValueError('Curator requires frozen Elasticsearch fixture')
        return False
    if (case['instance_id'] != 'elastic__curator-1437' or dependency['image'] != IMAGE
            or dependency['image_id'] != IMAGE_ID or dependency['version'] != '6.7.1'
            or dependency['container_user'] != '1000:1000'
            or dependency['memory_bytes'] != 1073741824 or dependency['cpus'] != 1
            or dependency['pids'] != 256 or dependency['heap'] != '256m'
            or dependency['host_ports'] != []):
        raise ValueError('Frozen Elasticsearch fixture differs')
    return True


def prepare_image(case):
    if not configured(case):
        return
    subprocess.run(['docker', 'pull', IMAGE], check=True, timeout=900)
    image = json.loads(subprocess.run(['docker', 'image', 'inspect', IMAGE], check=True,
                                     capture_output=True, text=True).stdout)[0]
    if image['Id'] != IMAGE_ID:
        raise ValueError('Elasticsearch config identity differs')


@contextmanager
def isolated_dependency(case, output):
    if not configured(case):
        yield '--network=none'
        return
    service = subprocess.run(['docker', 'run', '-d', '--user=1000:1000', '--network=none', '--cap-drop=ALL',
        '--security-opt=no-new-privileges', '--cpus=1', '--memory=1g', '--pids-limit=256',
        '-e', 'ES_JAVA_OPTS=-Xms256m -Xmx256m', '-e', 'discovery.type=single-node',
        '-e', 'cluster.name=qianora-dev17-base', '-e', 'network.host=127.0.0.1',
        '-e', 'http.port=9200', '-e', 'xpack.security.enabled=false',
        '-e', 'xpack.ml.enabled=false', IMAGE], check=True, capture_output=True, text=True, timeout=90).stdout.strip()
    if not re.fullmatch('[0-9a-f]{64}', service):
        raise ValueError('Unexpected new dependency container identity')
    try:
        yield '--network=container:' + service
    finally:
        try:
            state = json.loads(subprocess.run(['docker', 'inspect', service], check=True,
                                             capture_output=True, text=True).stdout)[0]
            receipt = {'image_id': state['Image'], 'container_user': state['Config']['User'],
                       'network_mode': state['HostConfig']['NetworkMode'],
                       'memory_bytes': state['HostConfig']['Memory'],
                       'port_bindings': state['HostConfig'].get('PortBindings'),
                       'oom_killed': state['State']['OOMKilled'], 'running': state['State']['Running']}
            (Path(output) / 'dependency-state.json').write_text(json.dumps(receipt, indent=2)+'\n')
            log = subprocess.run(['docker', 'logs', '--tail', '60', service], capture_output=True, text=True, timeout=20)
            (Path(output) / 'dependency-log-tail.txt').write_text((log.stdout + log.stderr)[-12000:])
        finally:
            subprocess.run(['docker', 'rm', '-f', service], check=True, stdout=subprocess.DEVNULL, timeout=30)


def ready(case):
    if not configured(case):
        return None
    for attempt in range(60):
        try:
            with urllib.request.urlopen('http://127.0.0.1:9200', timeout=2) as response:
                value = json.load(response)
            if value['version']['number'] != '6.7.1' or value['cluster_name'] != 'qianora-dev17-base':
                raise ValueError('Wrong fixed Elasticsearch service')
            return {'version': value['version']['number'], 'cluster_name': value['cluster_name'], 'ready': True}
        except OSError:
            time.sleep(1)
    raise ValueError('Isolated Elasticsearch service not ready')
