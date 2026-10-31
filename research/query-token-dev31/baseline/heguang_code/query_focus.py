"""A ranking-only view of a recognized structured issue; original queries survive.

Only trailing standard report/checklist sections are omitted, and only after
both expected and actual behavior headings. Fenced code is never interpreted
as report structure. Unknown formats retain the complete query.
"""
import re

POLICY = 'structured-behavior-query-v1'
HEADING = re.compile(r'^###[ \t]+([^\r\n]+?)[ \t]*\r?$')
FENCE = re.compile(r'^ {0,3}(`{3,}|~{3,})([^\r\n]*)\r?$')
TRAILING = {'system information', 'existing github issues'}


def focus_query(query):
    expected = actual = False
    fence = None
    offset = 0
    for line in query.splitlines(keepends=True):
        marker = FENCE.fullmatch(line.rstrip('\n'))
        if marker:
            token, suffix = marker.groups()
            if fence is None:
                fence = token[0], len(token)
            elif token[0] == fence[0] and len(token) >= fence[1] and not suffix.strip():
                fence = None
        elif fence is None:
            heading = HEADING.fullmatch(line.rstrip('\n'))
            if heading:
                title = heading[1].strip().rstrip(':').casefold()
                if title in TRAILING and expected and actual and offset >= 64:
                    return query[:offset].rstrip()
                expected |= title == 'expected behavior'
                actual |= title == 'actual behavior'
        offset += len(line)
    return query
