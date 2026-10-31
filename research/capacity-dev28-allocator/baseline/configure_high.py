"""Set only this test container's memory.high; never loosen a resource limit."""
import argparse
import json
import os
from pathlib import Path
import re


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pid',type=int,required=True)
    parser.add_argument('--container-id',required=True)
    args = parser.parse_args()
    assert os.geteuid() == 0 and args.pid > 1
    assert re.fullmatch('[0-9a-f]{64}',args.container_id)
    memberships = (Path('/proc')/str(args.pid)/'cgroup').read_text().splitlines()
    paths = [line.split(':',2)[2] for line in memberships if line.startswith('0::')]
    assert len(paths) == 1
    root = Path('/sys/fs/cgroup').resolve()
    group = (root/paths[0].lstrip('/')).resolve()
    assert group != root and group.is_relative_to(root)
    assert 'docker-'+args.container_id+'.scope' in group.parts or args.container_id in group.parts
    # Hard memory/swap bounds must already exist from container creation.
    assert int((group/'memory.max').read_text()) == 384*1024**2
    assert int((group/'memory.swap.max').read_text()) == 512*1024**2
    startup_peak = int((group/'memory.peak').read_text())
    assert startup_peak <= 320*1024**2
    path = group/'memory.high'
    existing = path.read_text().strip()
    assert existing in {'max',str(320*1024**2)}
    path.write_text(str(320*1024**2))
    assert int(path.read_text()) == 320*1024**2
    print(json.dumps({'container_id':args.container_id,'startup_peak_bytes':startup_peak,
        'memory_high':320*1024**2,'memory_max':384*1024**2,'memory_swap_max':512*1024**2,
        'other_groups_modified':False,'limits_raised':False}))


if __name__ == '__main__':
    main()
