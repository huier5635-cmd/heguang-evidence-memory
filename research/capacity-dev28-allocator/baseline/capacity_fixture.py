"""Self-owned inputs exercising the call-shape path; no benchmark targets."""
import json


def warm_fixture():
    prefix = 'Current call in capacity/lifecycle_cache.py.\n```python\n'
    code = 'cache = LifecycleCache()\n' + 'cache.lookup("warm_lifecycle_cache")\n'*31
    ending = '\n```'
    code += '#' + 'x'*(19900-len(prefix+code+ending)-1)
    query = {'user_id':'warm','query':prefix+code+ending,'top_k':100}
    old = ('# capacity/lifecycle_cache.py\nclass LifecycleCache:\n'
           '    def lookup(self, key, required_revision):\n        return key\n')
    new = old.replace('required_revision)', 'required_revision=None)')
    payload = {'user_id':'warm','session_id':'synthetic','request_id':'warm',
               'messages':[{'role':'user','content':old},{'role':'tool','content':new}]}
    return payload, query


def verify_fixture():
    from heguang_code.call_compatibility import query_calls, prioritize
    from heguang_code.selection import source_record
    payload, query = warm_fixture()
    assert len(query['query']) == 19900 and len(query_calls(query['query'])) == 32
    rows = []
    for index, message in enumerate(payload['messages']):
        body = message['content']
        meta = {'source_id':str(index),'session_id':'synthetic','role':message['role'],'characters':[0,len(body)]}
        rows.append({'id':str(index),'score':.5,'content':'[source '+json.dumps(meta)+']\n'+body})
    selected = prioritize(query['query'], rows)
    assert [source_record(row)[0]['source_id'] for row in selected] == ['1','0']
    return True
