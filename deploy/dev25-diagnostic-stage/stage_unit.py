"""Render a bounded loopback staging unit without applying server changes."""
import hashlib


def locations(manifest_bytes):
    identity = hashlib.sha256(manifest_bytes).hexdigest()[:12]
    return {'identity':identity,'release':'/opt/heguang-coding/staging/dev25diag-'+identity,
        'data':'/var/lib/heguang-coding-stage-dev25diag-'+identity,
        'unit':'heguang-coding-stage-dev25diag-'+identity+'.service'}


def render(manifest_bytes):
    paths = locations(manifest_bytes)
    release,data = paths['release'],paths['data']
    return '\n'.join([
        '[Unit]','Description=Qianora dev25+cycle1 isolated loopback verification','After=network-online.target','',
        '[Service]','Type=simple','User=admin','Group=admin','WorkingDirectory='+release,
        'EnvironmentFile=/etc/heguang-coding.env','Environment=TOKENIZERS_PARALLELISM=false',
        'Environment=OPENBLAS_NUM_THREADS=1','Environment=OMP_NUM_THREADS=1',
        'ExecStart='+release+'/venv/bin/python -I -B '+release+'/stage_runner.py',
        'Restart=no','RuntimeMaxSec=900','TimeoutStopSec=10','NoNewPrivileges=true','PrivateTmp=true',
        'ProtectSystem=strict','ProtectHome=true','ReadWritePaths='+data,'UMask=0077',
        'MemoryAccounting=true','MemoryHigh=320M','MemoryMax=384M','MemorySwapMax=512M',
        'CPUQuota=100%','TasksMax=64','LimitNOFILE=65536',''])
