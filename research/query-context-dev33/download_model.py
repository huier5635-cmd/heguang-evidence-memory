"""Download only six SHA-bound public model assets, never benchmark columns."""
import hashlib
import json
from pathlib import Path
import time
import urllib.request

ROOT=Path(__file__).resolve().parent

def main():
    receipt=json.loads((ROOT/'model-receipt.json').read_text('utf-8'))
    assert receipt['repository']=='cross-encoder/ms-marco-MiniLM-L6-v2'
    assert receipt['revision']=='233902d25c440f23af6f7d6e94d2946bac0bee0a' and len(receipt['files'])==6
    destination=ROOT/'model';destination.mkdir(exist_ok=False)
    for row in receipt['files']:
        path=(destination/row['path']).resolve();path.relative_to(destination.resolve())
        path.parent.mkdir(parents=True,exist_ok=True)
        assert row['url'].startswith('https://huggingface.co/'+receipt['repository']+'/resolve/'+receipt['revision']+'/')
        for attempt in range(3):
            temporary=path.with_suffix(path.suffix+'.part')
            try:
                size=0;digest=hashlib.sha256()
                with urllib.request.urlopen(row['url'],timeout=120) as response,temporary.open('xb') as output:
                    while part:=response.read(1024**2):
                        size+=len(part)
                        if size>row['bytes']:raise ValueError('Asset larger than pinned length')
                        digest.update(part);output.write(part)
                assert size==row['bytes'] and digest.hexdigest()==row['sha256']
                temporary.rename(path);break
            except Exception:
                temporary.unlink(missing_ok=True)
                if attempt==2:raise RuntimeError('Pinned public model acquisition failed') from None
                time.sleep(attempt+1)
    (destination/'receipt.json').write_bytes((ROOT/'model-receipt.json').read_bytes())
    print(json.dumps({'pinned_model_assets':6,'history_or_target_data_loaded':False,'credentials_used':False}))

if __name__=='__main__':main()
