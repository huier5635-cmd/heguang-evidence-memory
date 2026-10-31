"""Experimental completed-request GC plus release of unused glibc heap pages.

No model, source, storage, response or request budget changes. Unsupported
platforms retain GC-only behavior. This file is not approved for live use.
"""
import asyncio
import ctypes
import gc
import json
import sys
import time

def load_trim():
    if sys.platform!='linux':return None
    try:
        trim=ctypes.CDLL(None).malloc_trim
        trim.argtypes=[ctypes.c_size_t];trim.restype=ctypes.c_int
        return trim
    except (AttributeError,OSError):return None

TRIM=load_trim()

def release_unused_heap():
    started=time.monotonic()
    if TRIM is None:return {'available':False,'released':None,'seconds':0.0}
    result=int(TRIM(0))
    if result not in (0,1):raise RuntimeError('Unexpected allocator result')
    return {'available':True,'released':result,'seconds':round(time.monotonic()-started,6)}

def counter(record):
    print('QIANORA_LIFECYCLE '+json.dumps(record,sort_keys=True),flush=True)

def allocator_counter(record):
    print('QIANORA_ALLOCATOR '+json.dumps(record,sort_keys=True),flush=True)

class CompleteLargeRequest:
    def __init__(self,app,*,collector=gc.collect,sink=counter,reclaimer=release_unused_heap,allocator_sink=allocator_counter):
        self.app=app;self.collector=collector;self.sink=sink
        self.reclaimer=reclaimer;self.allocator_sink=allocator_sink;self.large=asyncio.Semaphore(1)

    def __getattr__(self,name):return getattr(self.app,name)

    @staticmethod
    def is_large(scope):
        if scope['type']!='http' or scope.get('path')!='/add':return False
        lengths=[v for k,v in scope.get('headers',[]) if k.lower()==b'content-length']
        return (len(lengths)!=1 or len(lengths[0])>20 or not lengths[0].isdigit() or int(lengths[0])>16*1024**2)

    def collect_and_release(self):
        collected=self.collector()
        try:allocation=self.reclaimer()
        except Exception as error:allocation={'error_type':type(error).__name__}
        return collected,allocation

    async def __call__(self,scope,receive,send):
        if not self.is_large(scope):return await self.app(scope,receive,send)
        async with self.large:
            try:return await self.app(scope,receive,send)
            finally:
                started=time.monotonic();allocation=None
                try:
                    collected,allocation=await asyncio.to_thread(self.collect_and_release)
                    record={'collected':collected,'seconds':round(time.monotonic()-started,6)}
                except Exception as error:record={'error_type':type(error).__name__}
                try:self.sink(record)
                except Exception:pass
                if allocation is not None:
                    try:self.allocator_sink(allocation)
                    except Exception:pass
