import asyncio
import gc
import sys
from pathlib import Path
import weakref

import pytest

sys.path.insert(0, str(Path(__file__).parent/'deployment'))
from request_lifecycle import CompleteLargeRequest


def scope(path='/add', length=b'20000000'):
    return {'type': 'http', 'path': path, 'headers': [(b'content-length', length)]}


async def receive():
    return {'type': 'http.request', 'body': b'synthetic', 'more_body': False}


def test_completed_application_cycles_are_gone_before_next_admission():
    refs, records, sent = [], [], []
    class Cycle:
        pass
    async def app(s, r, send):
        assert not refs or refs[-1]() is None
        obj = Cycle();obj.self = obj;refs.append(weakref.ref(obj))
        await r()
        await send({'type':'http.response.start','status':200,'headers':[]})
        await send({'type':'http.response.body','body':b'exact reply','more_body':False})
    async def send(frame):sent.append(frame)
    async def run():
        wrapped=CompleteLargeRequest(app,sink=records.append)
        await wrapped(scope(),receive,send)
        await wrapped(scope(),receive,send)
    enabled=gc.isenabled();gc.disable()
    try:asyncio.run(run())
    finally:
        if enabled:gc.enable()
    assert all(ref() is None for ref in refs)
    assert len(records)==2 and all(r['collected']>0 for r in records)
    assert sent==[{'type':'http.response.start','status':200,'headers':[]},
                  {'type':'http.response.body','body':b'exact reply','more_body':False}]*2


def test_sixteen_large_calls_serialize_through_complete_response_and_collection():
    active, peak, collected, completed = 0, 0, [], []
    async def app(s,r,send):
        nonlocal active,peak
        active+=1;peak=max(peak,active)
        await asyncio.sleep(0)
        await send({'type':'http.response.start','status':200,'headers':[]})
        await asyncio.sleep(0)
        await send({'type':'http.response.body','body':b'ok','more_body':False})
        completed.append(1);active-=1
    def collect():
        assert active==0 and len(completed)==len(collected)+1
        collected.append(1);return 0
    async def send(frame):pass
    async def run():
        wrapped=CompleteLargeRequest(app,collector=collect,sink=lambda row:None)
        await asyncio.gather(*(wrapped(scope(),receive,send) for _ in range(16)))
    asyncio.run(run())
    assert peak==1 and len(collected)==len(completed)==16


def test_health_and_small_request_are_available_while_a_large_response_is_pending():
    async def run():
        entered,release=asyncio.Event(),asyncio.Event();done=[]
        async def app(s,r,send):
            if s['path']=='/add' and s['headers'][0][1]==b'20000000':
                entered.set();await release.wait()
            done.append(s['path'])
        async def send(frame):pass
        records=[];wrapped=CompleteLargeRequest(app,collector=lambda:0,sink=records.append)
        large=asyncio.create_task(wrapped(scope(),receive,send));await entered.wait()
        await wrapped(scope('/health'),receive,send)
        await wrapped(scope(length=b'20'),receive,send)
        assert done==['/health','/add'] and not records
        release.set();await large
        assert len(records)==1
    asyncio.run(run())


def test_application_exception_and_sink_failure_preserve_transport_exception():
    error=RuntimeError('synthetic transport');called=[]
    async def app(s,r,send):raise error
    async def send(frame):pass
    def sink(row):raise ValueError('synthetic journal failure')
    def collect():called.append(1);return 0
    wrapped=CompleteLargeRequest(app,collector=collect,sink=sink)
    with pytest.raises(RuntimeError) as got:asyncio.run(wrapped(scope(),receive,send))
    assert got.value is error and called==[1]


@pytest.mark.parametrize('headers',[[],[(b'content-length',b'not decimal')],
    [(b'content-length',b'20'),(b'content-length',b'20')]])
def test_ambiguous_lengths_do_not_bypass_large_admission(headers):
    s=scope();s['headers']=headers
    assert CompleteLargeRequest.is_large(s)
