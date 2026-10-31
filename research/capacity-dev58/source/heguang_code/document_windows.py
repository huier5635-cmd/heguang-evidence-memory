"""Use a short technical query to expose otherwise unseen document context.

Original query/document tokens, stored evidence and head/tail model windows
are unchanged. Only the middle scoring window may become a compact continuous source interval. This is an
unpromoted mechanism hypothesis, not independent quality or repair evidence.
"""
from bisect import bisect_left,bisect_right
import re
from .features import IDENTIFIER,KEYWORDS,PATH
URL=re.compile(r'https?://[^\s<>]+')
MAX_ANCHORS=32
def anchor_spans(text):
    urls = [match.span() for match in URL.finditer(text)]
    spans = []
    seen = set()
    for kind, pattern in [(0, PATH), (1, IDENTIFIER)]:
        for match in pattern.finditer(text):
            if any((left <= match.start() < right for left, right in urls)):
                continue
            value = match.group()
            key = value.casefold()
            if kind == 1:
                tail = text[match.end():match.end() + 3].lstrip()
                before = text[max(0, match.start() - 1):match.start()]
                if len(value) <= 2 or key in KEYWORDS or (not ('_' in value or '::' in value or '.' in value or re.search('[a-z][A-Z]', value) or tail.startswith('(') or (before == '`'))):
                    continue
            if key in seen:
                continue
            seen.add(key)
            spans.append((kind, match.start(), match.end()))
            if len(spans) >= MAX_ANCHORS:
                return sorted(spans)
    return sorted(spans)

def passage_windows(query,text,ids,offsets,width):
    if type(width) is not int or not 0<width<=512:raise ValueError('Invalid model window width')
    total=len(ids)
    begins=sorted(set([0,max(0,(total-width)//2),max(0,total-width)]))
    parent=[(a,min(total,a+width)) for a in begins]
    if total<=width or len(parent)<3 or not isinstance(offsets,(list,tuple)) or len(offsets)!=total:return parent
    previous_start=previous_end=0
    for row in offsets:
        if not isinstance(row,(list,tuple)) or len(row)!=2 or any(type(v) is not int for v in row):return parent
        left,right=row
        if not 0<=left<=right<=len(text) or left<previous_start or right<previous_end:return parent
        previous_start,previous_end=left,right
    terms=[query[a:b] for _,a,b in anchor_spans(query) if b-a<=128]
    if not terms:return parent
    starts=[p[0] for p in offsets];ends=[p[1] for p in offsets]
    first_line=text.find('\n');body_start=first_line+1 if text.startswith('[source ') and first_line>=0 else 0
    matches=[]
    for term in terms:
        for hit in re.finditer(r'(?<!\w)'+re.escape(term)+r'(?!\w)',text[body_start:],re.I):
            left,right=hit.start()+body_start,hit.end()+body_start
            first=bisect_right(ends,left);last=bisect_left(starts,right)
            if first>=last or last-first>min(width,128):continue
            if offsets[first][0]>left or offsets[last-1][1]<right:continue
            matches.append((term.casefold(),left,right,first,last))
            if len(matches)>=256:break
        if len(matches)>=256:break
    if not matches:return parent
    choices=set()
    for _,left,right,first,last in matches:
        # Include the anchor's line, two prior lines and four following lines.
        # A long region is bounded to128original tokens around the anchor.
        a=text.rfind('\n',body_start,left)+1
        for _ in range(2):
            if a<=body_start:break
            a=max(body_start,text.rfind('\n',body_start,a-1)+1)
        b=text.find('\n',right)
        b=len(text) if b<0 else b+1
        for _ in range(4):
            if b>=len(text):break
            end=text.find('\n',b);b=len(text) if end<0 else end+1
        begin=bisect_right(ends,a);end=bisect_left(starts,b)
        maximum=min(width,128)
        if end-begin>maximum:
            begin=max(begin,min(total-maximum,(first+last)//2-maximum//2));end=min(total,begin+maximum)
        if begin<=first and last<=end and 0<end-begin<=maximum:choices.add((begin,end))
    if not choices:return parent
    def coverage(interval):
        a,b=interval;return len({term for term,_,_,first,last in matches if a<=first and last<=b})
    selected=min(choices,key=lambda pair:(-coverage(pair),pair[1]-pair[0],pair[0]))
    result=sorted(set([parent[0],selected,parent[-1]]))
    assert 1<=len(result)<=3 and all(0<=a<=b<=total and b-a<=width for a,b in result)
    return result
