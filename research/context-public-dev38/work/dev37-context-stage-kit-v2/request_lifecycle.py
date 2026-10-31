"""Release completed large-request cycles before admitting the next large call.

The application, bodies, replies, databases and ranking are unchanged. This is
an external lifecycle experiment, not an approved deployable memory candidate.
"""
import asyncio
import gc
import json
import time


def counter(record):
    print('QIANORA_LIFECYCLE ' + json.dumps(record, sort_keys=True), flush=True)


class CompleteLargeRequest:
    def __init__(self, app, *, collector=gc.collect, sink=counter):
        self.app = app
        self.collector = collector
        self.sink = sink
        self.large = asyncio.Semaphore(1)

    def __getattr__(self, name):
        return getattr(self.app, name)

    @staticmethod
    def is_large(scope):
        if scope['type'] != 'http' or scope.get('path') != '/add':
            return False
        lengths = [v for k, v in scope.get('headers', []) if k.lower() == b'content-length']
        # Unknown or ambiguous values use the conservative serialized path.
        return (len(lengths) != 1 or len(lengths[0]) > 20
                or not lengths[0].isdigit() or int(lengths[0]) > 16*1024**2)

    async def __call__(self, scope, receive, send):
        if not self.is_large(scope):
            return await self.app(scope, receive, send)
        async with self.large:
            try:
                return await self.app(scope, receive, send)
            finally:
                started = time.monotonic()
                try:
                    collected = await asyncio.to_thread(self.collector)
                    record = {'collected': collected, 'seconds': round(time.monotonic()-started, 6)}
                except Exception as error:
                    record = {'error_type': type(error).__name__}
                try:
                    self.sink(record)
                except Exception:
                    pass
