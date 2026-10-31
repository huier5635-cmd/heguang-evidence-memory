import hashlib
import importlib.util
from pathlib import Path

import pytest

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('stable_adapter',HERE/'stable_endpoint_adapter.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

def fixture():
    text=b'handle /health { respond "text" 200 }\n'
    alias=module.ORIGINAL_CODING.replace(b'/coding/*',b'/coding/dev25/*').replace(b'@coding_api',b'@diag_api').replace(b'67108864',b'134217728').replace(b'8752',b'8753')
    return b'example.invalid {\n'+alias+b'\n'+module.ORIGINAL_CODING+b'\n'+text+b'}\n',text

def test_exact_stable_alias_and_legacy_routes_preserve_unrelated_bytes():
    original,text=fixture();new=module.adapt(original,hashlib.sha256(original).hexdigest())
    assert text in new and new.count(text)==1
    assert new.count(b'handle_path /coding/dev6/* {')==1
    assert new.count(b'handle_path /coding/dev25/* {')==1 and new.count(b'handle_path /coding/* {')==1
    assert new.count(b'127.0.0.1:8753')==2 and new.count(b'127.0.0.1:8752')==1
    assert new.count(b'max_size 134217728')==2 and new.count(b'max_size 67108864')==1
    with pytest.raises(ValueError):module.adapt(new,hashlib.sha256(new).hexdigest())
    with pytest.raises(ValueError):module.adapt(original,'0'*64)

def test_unexpected_configuration_or_missing_alias_is_rejected():
    original,_=fixture()
    for current in [module.ORIGINAL_CODING,original.replace(b'127.0.0.1:8752',b'127.0.0.1:9999')]:
        with pytest.raises(ValueError):module.adapt(current,hashlib.sha256(current).hexdigest())
