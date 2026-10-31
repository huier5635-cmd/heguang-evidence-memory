"""Read only the validated test container's cgroup, without allocating inside it."""
import argparse
import json
from pathlib import Path
import re


def read_snapshot(pid, container_id, *, root=Path('/sys/fs/cgroup'), proc=Path('/proc')):
    if type(pid) is not int or pid <= 1 or not re.fullmatch('[0-9a-f]{64}', container_id):
        raise ValueError('Invalid own-container identity')
    memberships = (proc / str(pid) / 'cgroup').read_text().splitlines()
    paths = [line.split(':', 2)[2] for line in memberships if line.startswith('0::')]
    if len(paths) != 1:
        raise ValueError('Unexpected cgroup membership')
    root = root.resolve()
    group = (root / paths[0].lstrip('/')).resolve()
    if group == root or not group.is_relative_to(root):
        raise ValueError('Cgroup escaped private hierarchy')
    if 'docker-' + container_id + '.scope' not in group.parts and container_id not in group.parts:
        raise ValueError('Cgroup is not the requested own container')
    names = ['memory.current', 'memory.high', 'memory.max', 'memory.peak', 'memory.swap.current',
             'memory.swap.max', 'memory.swap.peak', 'memory.events', 'cpu.max', 'pids.max']
    values = {name: (group / name).read_text().strip() if (group / name).exists() else None for name in names}
    if (values['memory.high'] != str(320*1024**2) or values['memory.max'] != str(384*1024**2)
            or values['memory.swap.max'] != str(512*1024**2) or values['pids.max'] != '64'):
        raise ValueError('Test limits differ')
    quota, period = map(int, values['cpu.max'].split())
    if quota != period:
        raise ValueError('Test CPU limit differs')
    # Retain OOM/max/high counters even on failure; do not assert them away.
    return values


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid', type=int, required=True)
    parser.add_argument('--container-id', required=True)
    args = parser.parse_args()
    print(json.dumps(read_snapshot(args.pid, args.container_id), sort_keys=True))
