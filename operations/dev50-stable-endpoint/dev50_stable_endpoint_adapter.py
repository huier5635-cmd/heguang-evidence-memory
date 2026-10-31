"""Use the approved fixed URL after an explicit immutable-endpoint response."""
import hashlib
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent/'dev50-public-compact-template'))
from routing import MAIN,COMPACT
EXPECTED='766b73cf9c92e63ef0a1d8a3972c087822aefb7424b0e7dcb52fd0e552a5fb80'
NEW_MAIN=MAIN.replace(b'127.0.0.1:8755',b'127.0.0.1:8757')
def adapt(current,expected_sha256):
 if expected_sha256!=EXPECTED or hashlib.sha256(current).hexdigest()!=EXPECTED:
  raise ValueError('Current deployed compact routing differs')
 if current.count(MAIN)!=1 or current.count(COMPACT)!=1 or current.count(b'127.0.0.1:8757')!=1:
  raise ValueError('Unexpected active routing')
 for alias in [b'/coding/context/*',b'/coding/dev25/*',b'/coding/dev6/*']:
  if current.count(alias)!=1:raise ValueError('Missing preserved alias')
 changed=current.replace(MAIN,NEW_MAIN,1)
 if changed.replace(NEW_MAIN,MAIN,1)!=current:raise ValueError('Unrelated bytes changed')
 return changed
