"""Download only pinned public model/history assets. No target columns or credentials."""
import hashlib
import json
from pathlib import Path
import shutil
import time
import urllib.request

KIT = Path(__file__).resolve().parent


def fetch(row, destination):
    destination.resolve().relative_to(KIT.resolve())
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ValueError('Fresh asset destination required')
    for attempt in range(3):
        temporary = destination.with_suffix(destination.suffix + '.part')
        try:
            checksum, size = hashlib.sha256(), 0
            request = urllib.request.Request(row['url'], headers={'User-Agent': 'Qianora-public-capacity/1'})
            with urllib.request.urlopen(request, timeout=120) as incoming, temporary.open('xb') as output:
                while part := incoming.read(1024*1024):
                    size += len(part)
                    if size > row['bytes']:
                        raise ValueError('Asset larger than pinned length')
                    checksum.update(part)
                    output.write(part)
            if size != row['bytes'] or checksum.hexdigest() != row['sha256']:
                raise ValueError('Pinned public asset mismatch')
            temporary.rename(destination)
            return
        except Exception:
            temporary.unlink(missing_ok=True)
            if attempt == 2:
                raise RuntimeError('Pinned public asset acquisition failed') from None
            time.sleep(attempt+1)


def main():
    plan = json.loads((KIT/'input-plan.json').read_text(encoding='utf-8'))
    assert len(plan['files']) == 1 and not plan['full_allowed']
    incoming = KIT/'inputs'
    incoming.mkdir(exist_ok=False)
    row = plan['files'][0]
    fetch(row, incoming/row['local_path'])
    (incoming/'result.json').write_text(json.dumps(plan, indent=2)+'\n', encoding='utf-8')
    model = KIT/'model'
    model.mkdir(exist_ok=False)
    receipt = json.loads((KIT/'model-receipt.json').read_text(encoding='utf-8'))
    for item in receipt['files']:
        fetch(item, model/item['path'])
    shutil.copyfile(KIT/'model-receipt.json', model/'receipt.json')
    print(json.dumps({'public_history_verified_bytes':row['bytes'], 'pinned_model_files':len(receipt['files']),
        'target_records_loaded':False,'official_calls':0,'full_allowed':False}))


if __name__ == '__main__':
    main()
