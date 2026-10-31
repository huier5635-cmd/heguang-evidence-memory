"""Project an explicitly delimited historical PR description as an exact span."""
import hashlib
import json
from .selection import source_record

OPEN='<pr_description>'
CLOSE='</pr_description>'


def project_task_descriptions(rows):
    if len(rows)>104 or sum(len(r['content']) for r in rows)>600000:
        raise RuntimeError('Task projection candidate limit exceeded')
    result=[]
    for row in rows:
        meta,body=source_record(row)
        if (meta['role']!='user' or meta['characters'][0]!=0 or not body.startswith(OPEN)
                or body.count(OPEN)!=1 or body.count(CLOSE)!=1):
            result.append(row);continue
        end=body.index(CLOSE)
        trailing=body[end+len(CLOSE):].lstrip()
        # Recognize the historical workflow wrapper, rather than arbitrarily
        # shortening free-form user content or cutting an incomplete tag.
        if not trailing.startswith('<instructions>'):
            result.append(row);continue
        inner=body[len(OPEN):end];selected=inner.strip()
        if not selected:
            result.append(row);continue
        left=len(OPEN)+len(inner)-len(inner.lstrip());right=left+len(selected)
        metadata={**meta,'characters':[left,right],'content_projection':'historical_pr_description'}
        identity=hashlib.sha256(json.dumps([meta['source_id'],left,right],separators=(',',':')).encode()).hexdigest()[:36]
        result.append({**row,'id':'span_'+identity,'content':'[source '+json.dumps(metadata,ensure_ascii=False,separators=(',',':'))+']\n'+selected})
    return result
