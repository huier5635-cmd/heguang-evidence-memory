"""Bounded journal-only counters, without request identifiers or payload data."""
import json
import logging
import time

LOGGER = logging.getLogger('qianora.request_metadata')
if not LOGGER.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('%(message)s'))
    LOGGER.addHandler(handler)
LOGGER.setLevel(logging.INFO)
LOGGER.propagate = False


def emit(record):
    LOGGER.info('QIANORA_REQUEST ' + json.dumps(record, separators=(',', ':'), sort_keys=True))


class RequestMetadataMiddleware:
    def __init__(self, app, sink=None):
        self.app = app
        self.sink = emit if sink is None else sink

    async def __call__(self, scope, receive, send):
        path = scope.get('path')
        if scope['type'] != 'http' or path not in {'/add', '/search'}:
            return await self.app(scope, receive, send)
        start = time.monotonic()
        status = None
        completed = False
        received = 0
        response_bytes = 0
        state = scope.setdefault('state', {})

        async def counted_receive():
            nonlocal received
            message = await receive()
            if message['type'] == 'http.request':
                received += len(message.get('body', b''))
            return message

        async def counted_send(message):
            nonlocal status, completed, response_bytes
            if message['type'] == 'http.response.start':
                status = message['status']
            elif message['type'] == 'http.response.body':
                response_bytes += len(message.get('body', b''))
            await send(message)
            if message['type'] == 'http.response.body' and not message.get('more_body', False):
                completed = True

        try:
            await self.app(scope, counted_receive, counted_send)
        finally:
            record = {'operation': 'add' if path == '/add' else 'search',
                      'status': status, 'completed': completed,
                      'elapsed_ms': round((time.monotonic() - start) * 1000, 3),
                      'request_bytes': received, 'response_bytes': response_bytes}
            counts = state.get('qianora_counts', {})
            allowed = ('messages',) if path == '/add' else ('rows', 'characters')
            for name in allowed:
                value = counts.get(name)
                if type(value) is int and value >= 0:
                    record[name] = value
            try:
                self.sink(record)
            except Exception:
                # Journal availability must not change request processing or
                # replace its original exception with a diagnostic failure.
                pass
