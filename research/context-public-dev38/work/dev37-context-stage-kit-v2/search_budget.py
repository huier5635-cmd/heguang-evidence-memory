"""Experimental output profile matching the frozen local six-record/16k context.

The dev25 core, original query, authentication, storage and ranking are unchanged.
No deployment or quality gain is implied by preparing this adapter.
"""
import json

MAX_ROWS=6
MAX_CHARACTERS=16000
MAX_RESPONSE_BYTES=1024*1024
VERSION='0.2.0.dev25+ctx6x16k'

class InvalidUpstream(ValueError):pass

def select(payload,fit_budget):
    if not isinstance(payload,dict) or not isinstance(payload.get('data'),list):
        raise InvalidUpstream('Missing memory array')
    for row in payload['data']:
        if (not isinstance(row,dict) or not isinstance(row.get('id'),str) or not row['id']
                or not isinstance(row.get('content'),str) or not row['content']):
            raise InvalidUpstream('Invalid original memory record')
    result=fit_budget(payload['data'],top_k=MAX_ROWS,characters=MAX_CHARACTERS)
    if len(result)>MAX_ROWS or sum(len(row['content']) for row in result)>MAX_CHARACTERS:
        raise InvalidUpstream('Context budget exceeded')
    return {**payload,'data':result}

class BudgetedSearch:
    def __init__(self,app,*,fit_budget):self.app=app;self.fit_budget=fit_budget
    def __getattr__(self,name):return getattr(self.app,name)

    async def __call__(self,scope,receive,send):
        path=scope.get('path')
        if scope['type']!='http' or path not in {'/search','/health'}:
            return await self.app(scope,receive,send)
        start=None;body=bytearray();capturing=False;sent=False
        async def emit(message):
            nonlocal start,capturing,sent
            if message['type']=='http.response.start':
                if start is not None or sent:raise InvalidUpstream('Duplicate response start')
                start=message
                if message['status']!=200:
                    sent=True;await send(message);return
                headers={key.lower():value.lower() for key,value in message.get('headers',[])}
                if headers.get(b'content-type',b'').split(b';',1)[0].strip()!=b'application/json':
                    raise InvalidUpstream('Successful response is not JSON')
                if headers.get(b'content-encoding',b'identity')!=b'identity':
                    raise InvalidUpstream('Unexpected encoded upstream response')
                capturing=True;return
            if not capturing:
                await send(message);return
            if message['type']!='http.response.body':raise InvalidUpstream('Unexpected response event')
            part=message.get('body',b'')
            if len(body)+len(part)>MAX_RESPONSE_BYTES:raise InvalidUpstream('Upstream response exceeds bound')
            body.extend(part)
            if message.get('more_body',False):return
            try:
                payload=json.loads(body)
                if path=='/search':payload=select(payload,self.fit_budget)
                else:
                    if not isinstance(payload,dict) or payload.get('version')!='0.2.0.dev25':
                        raise InvalidUpstream('Core version differs')
                    payload={**payload,'version':VERSION,'core_version':'0.2.0.dev25',
                        'core_evidence_budget_characters':payload.get('evidence_budget_characters'),
                        'evidence_budget_characters':MAX_CHARACTERS,'max_memory_records':MAX_ROWS}
                encoded=json.dumps(payload,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf-8')
            except (ValueError,TypeError,UnicodeError,KeyError) as error:
                raise InvalidUpstream('Invalid upstream memory response') from error
            headers=[(key,value) for key,value in start.get('headers',[]) if key.lower()!=b'content-length']
            headers.append((b'content-length',str(len(encoded)).encode('ascii')))
            await send({**start,'headers':headers});sent=True;capturing=False
            await send({'type':'http.response.body','body':encoded,'more_body':False})
            body.clear()
        try:
            await self.app(scope,receive,emit)
            if not sent:raise InvalidUpstream('Upstream response was not completed')
        except InvalidUpstream:
            if sent:raise
            body.clear()
            encoded=b'{"detail":{"reason":"Invalid upstream memory response"}}'
            await send({'type':'http.response.start','status':503,'headers':[
                (b'content-type',b'application/json'),(b'content-length',str(len(encoded)).encode('ascii'))]})
            await send({'type':'http.response.body','body':encoded,'more_body':False})
