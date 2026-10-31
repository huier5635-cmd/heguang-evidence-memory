"""Keep source-local issue context around otherwise lost middle technical names.

Only model input token selection changes. Original queries and memory evidence
are neither rewritten nor logged. Short queries and unsupported offsets retain
the parent's behavior. This is an unpromoted research candidate.
"""
from bisect import bisect_left,bisect_right
import re

from .features import IDENTIFIER,KEYWORDS,PATH

LIMIT=192
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
    parent=set(range(128))|set(range(len(ids)-64,len(ids)))
    mapped=[];mandatory=set()
    for kind,left,right in anchor_spans(text):
        first=bisect_right(ends,left);last=bisect_left(starts,right)
        if (first>=last or last-first>MAX_ANCHOR_TOKENS
                or offsets[first][0]>left or offsets[last-1][1]<right):continue
        anchor=set(range(first,last));mapped.append((kind,left,right,first,last,anchor))
        if anchor<=parent:mandatory.update(anchor)
    fresh=[row for row in mapped if not row[-1]<=parent]
    if not fresh or len(mandatory)>64:return fallback
    structural={'expected behavior','actual behavior','description','issue','bug report',
        'system information','environment','steps to reproduce','additional context'}
    header=set();offset=0
    for line in text.splitlines(keepends=True):
        visible=line.strip()
        if visible:
            label=visible.lstrip('# ').rstrip(':').casefold()
            if len(visible)<=120 and len(re.findall(r'\w+',visible))>=2 and label not in structural:
                first=bisect_right(ends,offset);last=bisect_left(starts,offset+len(line.rstrip('\r\n')))
                if last-first<=32:header.update(range(first,last))
            break
        offset+=len(line)
    chosen=mandatory|header
    if len(chosen)>96:return fallback
    boundaries=sorted(set([0,len(text),*[match.end() for match in re.finditer(r'(?<=[.!?])[ \t]+|\n',text)]]))
    changed=False
    for _,left,right,first,last,anchor in fresh:
        if anchor<=chosen:continue
        a=boundaries[max(0,bisect_right(boundaries,left)-1)]
        b=boundaries[min(len(boundaries)-1,bisect_left(boundaries,right)+1)]
        begin=bisect_right(ends,a);end=bisect_left(starts,b)
        maximum=min(160,LIMIT-len(chosen))
        if end-begin>maximum:
            begin=max(begin,first-24);end=min(end,begin+maximum)
            if end<last:end=last;begin=max(0,end-maximum)
        region=set(range(begin,end))
        if not anchor<=region or len(chosen|region)>LIMIT:region=anchor
        if len(chosen|region)>LIMIT:continue
        chosen.update(region);changed=True
    if not changed:return fallback
    assert 0<len(chosen)<=LIMIT
    return [ids[index] for index in sorted(chosen)]
