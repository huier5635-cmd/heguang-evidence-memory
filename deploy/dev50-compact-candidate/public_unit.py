"""Render a separate, bounded service for the diagnostic version."""
import hashlib

def locations(manifest_bytes):
    identity=hashlib.sha256(manifest_bytes).hexdigest()[:12]
    return {'identity':identity,'release':'/opt/heguang-coding/public/dev45ctx50-'+identity,
        'data':'/var/lib/heguang-coding-public-dev45ctx50-'+identity,
        'unit':'heguang-coding-public-dev45ctx50-'+identity+'.service'}

def render(manifest_bytes):
    from public_manifest import PARENT
    paths=locations(manifest_bytes);digest=hashlib.sha256(manifest_bytes).hexdigest()
    return '\n'.join(['[Unit]','Description=Qianora dev45 compact-context Smoke candidate service',
        'After=network-online.target','StartLimitIntervalSec=60','StartLimitBurst=3','',
        '[Service]','Type=simple','User=admin','Group=admin','WorkingDirectory='+paths['release'],
        'EnvironmentFile=/etc/heguang-coding.env','Environment=QIANORA_PUBLIC_MANIFEST_SHA='+digest,
        'Environment=TOKENIZERS_PARALLELISM=false','Environment=OPENBLAS_NUM_THREADS=1','Environment=OMP_NUM_THREADS=1',
        'ExecStart='+str(PARENT)+'/venv/bin/python -I -B '+paths['release']+'/public_runner.py',
        'Restart=on-failure','RestartSec=3','TimeoutStopSec=10','NoNewPrivileges=true','PrivateTmp=true',
        'ProtectSystem=strict','ProtectHome=true','ReadWritePaths='+paths['data'],'UMask=0077',
        'MemoryAccounting=true','MemoryHigh=320M','MemoryMax=384M','MemorySwapMax=512M','CPUQuota=100%',
        'TasksMax=64','LimitNOFILE=65536','','[Install]','WantedBy=multi-user.target',''])
