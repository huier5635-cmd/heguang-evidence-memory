"""Choose bounded original indexed diff intervals, never construct a patch."""
import re
from .document_windows import anchor_spans

def select_diff_bounds(store,db,source,query,width):
    if type(width) is not int or not 1<=width<=8192:raise ValueError('Invalid historical diff width')
    indexed=[(row[0],row[1]) for row in db.execute('SELECT start,end FROM diff_spans WHERE source_id=? ORDER BY start,end',(source['id'],))]
    if not indexed:return []
    if len(indexed)>64 or any(type(a) is not int or type(b) is not int or not 0<=a<b<=source['chars'] or b-a>width for a,b in indexed):
        raise RuntimeError('Invalid historical diff intervals')
    left=min(a for a,b in indexed);right=max(b for a,b in indexed)
    parent=[(left,right)] if right-left<=width else [(left,left+width),(right-width,right)]
    terms=list(dict.fromkeys(query[a:b].casefold() for _,a,b in anchor_spans(query) if b-a<=128))
    if not terms:return parent
    patterns=[re.compile(r'(?<!\w)'+re.escape(term)+r'(?!\w)',re.I) for term in terms]
    ranked=[];cache={}
    for a,b in indexed:
        text=store.source_span(db,source['id'],a,b,cache)
        coverage=frozenset(i for i,p in enumerate(patterns) if p.search(text))
        if coverage:ranked.append((coverage,a,b))
    if not ranked:return parent
    ranked.sort(key=lambda row:(-len(row[0]),row[1],row[2]))
    selected=[(ranked[0][1],ranked[0][2])];covered=set(ranked[0][0])
    for coverage,a,b in ranked[1:]:
        if not set(coverage)-covered:continue
        selected.append((a,b));break
    # One best original chunk is sufficient when every named anchor it can
    # explain is already covered. Never fill the second slot with unrelated text.
    return selected
