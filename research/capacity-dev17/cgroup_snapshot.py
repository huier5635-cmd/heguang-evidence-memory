"""Read only this container's Linux cgroup counters."""
import json
from pathlib import Path

root = Path('/sys/fs/cgroup')
fields = ['memory.max','memory.high','memory.current','memory.peak','memory.swap.max',
          'memory.swap.current','memory.swap.peak','memory.events','cpu.max','pids.max']
result = {name:(root/name).read_text().strip() if (root/name).exists() else None for name in fields}
print(json.dumps(result,sort_keys=True))
