"""Deployment-only counters around the frozen dev16 application.

No request payload, identity, query, credential or exception text is logged.
The original Store and application implement every data/response operation.
"""
from contextvars import ContextVar
import json
import logging
import time

from heguang_code.api import create_app as frozen_create_app

COUNTS = ContextVar('qianora_deployment_counts',default=None)
MAX_RESPONSE_CAPTURE = 1024*1024
LOGGER = logging.getLogger('qianora.request_metadata')
if not LOGGER.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(message)s'))
    LOGGER.addHandler(handler)
LOGGER.setLevel(logging.INFO)
LOGGER.propagate = False


def emit(record):
    LOGGER.info('QIANORA_REQUEST '+json.dumps(record,separators=(',',':'),sort_keys=True))


class MetadataMiddleware:
    def __init__(self,app,sink=None):
        self.app = app
        self.sink = emit if sink is None else sink

    async def __call__(self,scope,receive,send):
        path = scope.get('path')
        if scope['type'] != 'http' or path not in {'/add','/search'}:
            return await self.app(scope,receive,send)
        counts = {}
        token = COUNTS.set(counts)
        started = time.monotonic()
        status,completed,received,response_bytes = None,False,0,0
        pieces,captured,overflow = [],0,False

        async def counted_receive():
            nonlocal received
            message = await receive()
            if message['type'] == 'http.request':
                received += len(message.get('body',b''))
            return message

        async def counted_send(message):
            nonlocal status,completed,response_bytes,captured,overflow
            if message['type'] == 'http.response.start':
                status = message['status']
            elif message['type'] == 'http.response.body':
                body = message.get('body',b'')
                response_bytes += len(body)
                if path == '/search' and status == 200 and not overflow:
                    captured += len(body)
                    if captured <= MAX_RESPONSE_CAPTURE:
                        pieces.append(body)
                    else:
                        overflow = True
                        pieces.clear()
            # Forward exactly the original ASGI message, before diagnostics.
            await send(message)
            if message['type'] == 'http.response.body' and not message.get('more_body',False):
                completed = True

        try:
            return await self.app(scope,counted_receive,counted_send)
        finally:
            record = {'operation':'add' if path == '/add' else 'search',
                'status':status,'completed':completed,'elapsed_ms':round((time.monotonic()-started)*1000,3),
                'request_bytes':received,'response_bytes':response_bytes}
            try:
                if path == '/add' and type(counts.get('messages')) is int:
                    record['messages'] = counts['messages']
                if path == '/search' and status == 200 and completed and not overflow:
                    value = json.loads(b''.join(pieces))
                    rows = value.get('data') if isinstance(value,dict) else None
                    if (isinstance(rows,list) and len(rows)<=100 and all(isinstance(row,dict)
                            and isinstance(row.get('content'),str) for row in rows)):
                        record.update(rows=len(rows),characters=sum(len(row['content']) for row in rows))
            except Exception:
                # Counters cannot replace or alter a completed response.
                pass
            finally:
                pieces.clear()
                COUNTS.reset(token)
            try:
                self.sink(record)
            except Exception:
                pass


def create_app(settings=None,scorer=None,metadata_sink=None):
    app = frozen_create_app(settings,scorer)
    original_add = app.state.store.add

    def counted_add(request):
        counts = COUNTS.get()
        if counts is not None:
            # asyncio.to_thread copies the context but shares this dictionary.
            # This is a validated count, including writes later rejected as409.
            counts['messages'] = len(request.messages)
        return original_add(request)

    app.state.store.add = counted_add
    from search_budget import BudgetedSearch
    from heguang_code.reranker import fit_budget
    app.add_middleware(BudgetedSearch,fit_budget=fit_budget)
    app.add_middleware(MetadataMiddleware,sink=metadata_sink)
    return app
