"""Preserve complete middle technical names in the existing192-token rank view.

Only model input token selection changes. Original queries and memory evidence
are neither rewritten nor logged. Short queries and unsupported offsets retain
the parent's behavior. This is an unpromoted research candidate.
"""
from bisect import bisect_left,bisect_right
import re

from .features import IDENTIFIER,KEYWORDS,PATH

LIMIT=192
PREFIX=96
SUFFIX=32
MAX_ANCHORS=32
MAX_ANCHOR_TOKENS=32
URL=re.compile(r'https?://[^\s<>]+')

def anchor_spans(text):
    urls=[match.span() for match in URL.finditer(text)]
    spans=[];seen=set()
    for kind,pattern in [(0,PATH),(1,IDENTIFIER)]:
        for match in pattern.finditer(text):
            if any(left<=match.start()<right for left,right in urls):continue
            value=match.group();key=value.casefold()
            if kind==1:
                tail=text[match.end():match.end()+3].lstrip()
                before=text[max(0,match.start()-1):match.start()]
                if (len(value)<=2 or key in KEYWORDS or not ('_' in value or '::' in value or '.' in value
                    or re.search(r'[a-z][A-Z]',value) or tail.startswith('(') or before=='`')):continue
            if key in seen:continue
            seen.add(key);spans.append((kind,match.start(),match.end()))
            if len(spans)>=MAX_ANCHORS:return sorted(spans)
    return sorted(spans)

def preserve_query_anchors(text,ids,offsets):
    if len(ids)<=LIMIT:return list(ids)
    fallback=list(ids[:128])+list(ids[-64:])
    if offsets is None or len(offsets)!=len(ids):return fallback
    previous_start=previous_end=0
    for row in offsets:
        if (not isinstance(row,(list,tuple)) or len(row)!=2
                or any(type(value) is not int for value in row)):
            return fallback
        left,right=row
        if not 0<=left<=right<=len(text) or left<previous_start or right<previous_end:return fallback
        previous_start,previous_end=left,right
    starts=[row[0] for row in offsets];ends=[row[1] for row in offsets]
    parent_indices=list(range(128))+list(range(len(ids)-64,len(ids)))
    parent=set(parent_indices)
    chosen=set(range(PREFIX))|set(range(len(ids)-SUFFIX,len(ids)))
    changed=False
    for _,left,right in anchor_spans(text):
        first=bisect_right(ends,left);last=bisect_left(starts,right)
        if (first>=last or last-first>MAX_ANCHOR_TOKENS
                or offsets[first][0]>left or offsets[last-1][1]<right):continue
        anchor=set(range(first,last))
        if anchor<=parent or len(chosen|anchor)>LIMIT:continue
        chosen.update(anchor);changed=True
    if not changed:return fallback
    # Fill spare slots from the unchanged parent view, preserving source order.
    for index in parent_indices:
        if len(chosen)==LIMIT:break
        chosen.add(index)
    assert len(chosen)==LIMIT
    return [ids[index] for index in sorted(chosen)]
