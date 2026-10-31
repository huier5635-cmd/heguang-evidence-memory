"""Locate and stream an exact messages array from an already pinned JSON file.

This is a byte-preserving replay helper, not a general JSON validator. The
complete file SHA must match a previously validated public trajectory first.
Quoted strings are skipped with native find, without decoding giant stdout.
"""
import hashlib
import json
import mmap
from pathlib import Path
import re

STRUCTURAL = re.compile(br'["\[\]{}]')
SPACE = b' \t\r\n'


def _space(data,position):
    while position < len(data) and data[position] in SPACE:
        position += 1
    return position


def _string_end(data,start):
    assert data[start] == 34
    position = start+1
    while True:
        quote = data.find(b'"',position)
        if quote < 0:
            raise ValueError('Unterminated pinned JSON string')
        left = quote-1
        while left>start and data[left] == 92:
            left -= 1
        if (quote-left-1)%2 == 0:
            return quote+1
        position = quote+1


def _end(data,start):
    if data[start] == 34:
        return _string_end(data,start)
    if data[start] not in (91,123):
        position = start
        while position<len(data) and data[position] not in b',} \t\r\n':
            position += 1
        if position==start:
            raise ValueError('Missing pinned JSON value')
        return position
    closing = {91:93,123:125}
    stack = [closing[data[start]]]
    position = start+1
    while stack:
        found = STRUCTURAL.search(data,position)
        if found is None:
            raise ValueError('Unterminated pinned JSON container')
        position = found.start()
        symbol = data[position]
        if symbol == 34:
            position = _string_end(data,position)
            continue
        if symbol in closing:
            stack.append(closing[symbol])
        elif symbol != stack.pop():
            raise ValueError('Mismatched pinned JSON container')
        position += 1
    return position


def messages_bounds(path,expected_sha256):
    path = Path(path)
    if not re.fullmatch('[0-9a-f]{64}',expected_sha256):
        raise ValueError('Pinned SHA256 required')
    with path.open('rb') as stream:
        if hashlib.file_digest(stream,'sha256').hexdigest() != expected_sha256:
            raise ValueError('Pinned public trajectory changed')
        if path.stat().st_size == 0:
            raise ValueError('Empty pinned trajectory')
        with mmap.mmap(stream.fileno(),0,access=mmap.ACCESS_READ) as data:
            position = _space(data,0)
            if position>=len(data) or data[position] != 123:
                raise ValueError('Pinned trajectory must be an object')
            position += 1
            keys,result = set(),None
            while True:
                position = _space(data,position)
                if position>=len(data) or data[position] != 34:
                    raise ValueError('Pinned root key missing')
                end = _string_end(data,position)
                if end-position>256:
                    raise ValueError('Unexpected pinned root key size')
                key = json.loads(data[position:end])
                if key in keys:
                    raise ValueError('Duplicate pinned root key')
                keys.add(key)
                position = _space(data,end)
                if position>=len(data) or data[position] != 58:
                    raise ValueError('Pinned key colon missing')
                start = _space(data,position+1)
                if start>=len(data):
                    raise ValueError('Pinned value missing')
                end = _end(data,start)
                if key == 'messages':
                    if data[start] != 91:
                        raise ValueError('Pinned messages must be an array')
                    result = (start,end)
                position = _space(data,end)
                if position>=len(data) or data[position] not in (44,125):
                    raise ValueError('Pinned object separator missing')
                if data[position] == 125:
                    if _space(data,position+1) != len(data):
                        raise ValueError('Unexpected pinned trailing data')
                    break
                position += 1
            if result is None:
                raise ValueError('Pinned messages missing')
            return result


def replay_parts(path,bounds,envelope):
    if set(envelope) != {'user_id','session_id','request_id'}:
        raise ValueError('Replay envelope differs from Add contract')
    if any(not isinstance(value,str) or not 0<len(value)<=1024 for value in envelope.values()):
        raise ValueError('Bounded replay identifiers required')
    prefix = json.dumps(envelope,ensure_ascii=False,separators=(',',':')).encode()[:-1]+b',"messages":'
    start,end = bounds
    if not 0<=start<end<=Path(path).stat().st_size:
        raise ValueError('Pinned byte interval invalid')
    length = len(prefix)+end-start+1

    def chunks():
        yield prefix
        with Path(path).open('rb') as stream:
            stream.seek(start)
            remaining = end-start
            while remaining:
                part = stream.read(min(65536,remaining))
                if not part:
                    raise ValueError('Pinned replay truncated')
                remaining -= len(part)
                yield part
        yield b'}'

    return length,chunks()
