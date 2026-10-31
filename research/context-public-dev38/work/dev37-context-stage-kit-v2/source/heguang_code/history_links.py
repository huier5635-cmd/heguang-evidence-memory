"""Link retrieved historical sessions to their original stored diff passages."""
from collections import deque
import hashlib
import json
import re
from .selection import source_record

DIFF = re.compile(r'^diff --git [^\n]+\n', re.M)
HUNK = re.compile(r'^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@[^\n]*(?:\n|$)', re.M)
WIDTH = 8192


def diff_spans(text):
    """Retain bounded offsets into actual unified diffs, never copied bodies.

    Only the final 64 passages are indexed; all original text remains stored.
    Require an actual hunk and added/removed line, not a command or prose label.
    """
    spans = deque(maxlen=64)
    starts = (m.start() for m in DIFF.finditer(text))
    previous = next(starts, None)
    if previous is None:
        return []
    def consume(start, end):
        hunk = HUNK.search(text, start, end)
        if hunk is None:
            return
        if hunk.start()-start>WIDTH:
            return
        prefix=text[start:hunk.start()]
        if not re.search(r'^--- ',prefix,re.M) or not re.search(r'^\+\+\+ ',prefix,re.M):
            return
        changed=False;patch_end=None
        while hunk is not None:
            old=int(hunk.group(1)) if hunk.group(1) is not None else 1
            new=int(hunk.group(2)) if hunk.group(2) is not None else 1
            cursor=hunk.end()
            while old or new:
                if cursor>=end:return
                newline=text.find('\n',cursor,end)
                right=end if newline<0 else newline+1
                marker=text[cursor]
                if marker=='-':old-=1;changed=True
                elif marker=='+':new-=1;changed=True
                elif marker==' ':old-=1;new-=1
                elif text.startswith('\\ No newline at end of file',cursor):
                    cursor=right;continue
                else:return
                if old<0 or new<0:return
                cursor=right
            if text.startswith('\\ No newline at end of file',cursor):
                newline=text.find('\n',cursor,end);cursor=end if newline<0 else newline+1
            patch_end=cursor
            hunk=HUNK.match(text,cursor,end)
        if not changed or patch_end is None:return
        for left in range(start,patch_end,WIDTH):
            right=min(patch_end,left+WIDTH)
            spans.append((left,right))
    for current in starts:
        consume(previous,current);previous=current
    consume(previous,len(text))
    return list(spans)


def link_diffs(store, req, rows):
    if len(rows)>100 or sum(len(r['content']) for r in rows)>512000:
        raise RuntimeError('Historical link candidate limit exceeded')
    if not rows:
        return rows
    db=store.connect(req.user_id,create=False)
    if db is None:
        return rows
    try:
        anchors={}
        for row in rows[:6]:
            meta,_=source_record(row)
            # Re-resolve the source in this user's database. Caller-provided
            # session headers cannot authorize another user's or session's text.
            source=db.execute('SELECT session_id FROM sources WHERE id=?',(meta['source_id'],)).fetchone()
            if source is not None and source['session_id']==meta['session_id']:
                anchors.setdefault(meta['session_id'],row['id'])
            if len(anchors)==2:break
        links={};cache={}
        for session,anchor in anchors.items():
            source=db.execute("""SELECT s.* FROM sources s WHERE s.session_id=?
                AND EXISTS(SELECT 1 FROM diff_spans d WHERE d.source_id=s.id)
                ORDER BY s.added_at DESC,s.rowid DESC LIMIT 1""",(session,)).fetchone()
            linked=[]
            if source is None:
                links[anchor]=linked
                continue
            bounds=db.execute('SELECT min(start),max(end) FROM diff_spans WHERE source_id=?',(source['id'],)).fetchone()
            left,right=bounds
            intervals=[(left,right)] if right-left<=WIDTH else [(left,left+WIDTH),(right-WIDTH,right)]
            for start,end in intervals:
                meta={'source_id':source['id'],'session_id':session,'role':source['role'],'characters':[start,end]}
                if source['timestamp'] is not None:meta['timestamp_ms']=source['timestamp']
                if source['representation']!='content':meta['representation']=source['representation']
                if source['paired_source_id'] is not None:meta['paired_source_id']=source['paired_source_id']
                elif source['representation']=='trajectory_step_v1':meta['paired_parent_expired']=True
                body=store.source_span(db,source['id'],start,end,cache)
                identity=hashlib.sha256(json.dumps([source['id'],start,end],separators=(',',':')).encode()).hexdigest()[:36]
                linked.append({'id':'span_'+identity,'content':'[source '+json.dumps(meta,ensure_ascii=False,separators=(',',':'))+']\n'+body})
            links[anchor]=linked
        output=[];covered={};emitted_bodies={}
        linked_bodies={row['content'].split('\n',1)[1] for group in links.values() for row in group}
        def emit(row,is_link=False):
            meta,body=source_record(row);left,right=meta['characters']
            if (is_link or body in linked_bodies) and any(body in old for old in emitted_bodies.get(meta['session_id'],[])):return
            intervals=covered.setdefault(meta['source_id'],[])
            overlap=sum(max(0,min(right,b)-max(left,a)) for a,b in intervals)
            if overlap>0.8*(right-left):return
            intervals.append((left,right));output.append(row)
            emitted_bodies.setdefault(meta['session_id'],[]).append(body)
        for row in rows:
            emit(row)
            for linked in links.get(row['id'],[]):
                emit({**linked,'score':row['score']},is_link=True)
        return output
    finally:
        db.close()
