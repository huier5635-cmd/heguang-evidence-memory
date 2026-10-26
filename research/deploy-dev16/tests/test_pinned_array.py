import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pinned_array import messages_bounds,replay_parts

ENVELOPE = {'user_id':'synthetic-user','session_id':'synthetic-session','request_id':'synthetic-request'}


@pytest.mark.parametrize('order',[0,1,2])
def test_exact_array_preserves_nested_escaped_unicode_and_chunk_boundaries(tmp_path,order):
    messages = [{'role':'user','content':'中😀 [ ] { } " \\ \\" \n'*9000,'extra':{'nested':[{'messages':'not the root key'}]}}]
    pairs = [('before',{'messages':[1,2], 'quoted':'"messages": [wrong]'}),('messages',messages),('after',[None,True,2.5,'] }'])]
    pairs = pairs[order:]+pairs[:order]
    raw = json.dumps(dict(pairs),ensure_ascii=False,indent=2).encode()
    path = tmp_path/'source.json';path.write_bytes(raw)
    bounds = messages_bounds(path,hashlib.sha256(raw).hexdigest())
    expected_array = raw[bounds[0]:bounds[1]]
    assert json.loads(expected_array)==messages
    length,parts = replay_parts(path,bounds,ENVELOPE)
    chunks = list(parts)
    assert max(map(len,chunks))<=65536
    result = b''.join(chunks)
    assert len(result)==length and expected_array in result
    assert json.loads(result)=={**ENVELOPE,'messages':messages}


@pytest.mark.parametrize('raw',[
    b'{"messages":[],"messages":[]}', b'{"metadata":{"messages":[]}}',
    b'{"messages":"not an array"}', b'{"messages":[}',
    b'{"messages":[{"a":"unterminated}]}', b'{"messages":[]} trailing',
])
def test_ambiguous_or_structurally_unusable_sources_fail(tmp_path,raw):
    path = tmp_path/'source.json';path.write_bytes(raw)
    with pytest.raises(ValueError):messages_bounds(path,hashlib.sha256(raw).hexdigest())


def test_pinned_source_hash_and_envelope_scope_are_required(tmp_path):
    path = tmp_path/'source.json';raw=b'{"messages":[]}';path.write_bytes(raw)
    with pytest.raises(ValueError,match='changed'):messages_bounds(path,'0'*64)
    bounds = messages_bounds(path,hashlib.sha256(raw).hexdigest())
    with pytest.raises(ValueError,match='contract'):replay_parts(path,bounds,{**ENVELOPE,'unexpected':'value'})
    with pytest.raises(ValueError,match='interval'):replay_parts(path,(0,10000),ENVELOPE)
