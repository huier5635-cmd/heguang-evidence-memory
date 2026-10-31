"""Keep one exact, complete historical diff body per session, in rank order."""
import hashlib
import json

from .history_links import diff_spans
from .selection import source_record

OUTPUT = '\n<returncode>0</returncode>\n<output>\n'
END = '</output>'


def _whole_diff(text):
    if not text.startswith('diff --git '):
        return False
    spans = diff_spans(text)
    cursor = 0
    for left, right in spans:
        if left != cursor:
            return False
        cursor = right
    return bool(spans) and cursor == len(text)


def project_diff_only(rows):
    """Remove only recognized wrappers, preserving the selected original span.

    Nonzero exits, extra logs/prose, incomplete diffs, ordinary observations and
    partial sources are unchanged. An old diff remains historical evidence;
    success of a command never certifies that the current task is repaired.
    """
    if len(rows) > 104 or sum(len(r['content']) for r in rows) > 600000:
        raise RuntimeError('Patch projection candidate limit exceeded')
    result, seen = [], set()
    for row in rows:
        metadata, body = source_record(row)
        left, right = 0, len(body)
        if metadata['role'] == 'tool' and metadata['characters'][0] == 0:
            if (not body.startswith('Command:\n') or body.count(OUTPUT) != 1
                    or body.count('<returncode>') != 1 or body.count('<output>') != 1
                    or not body.endswith(END)):
                result.append(row)
                continue
            left = body.index(OUTPUT) + len(OUTPUT)
            right -= len(END)
        elif metadata['role'] != 'exit':
            result.append(row)
            continue
        selected = body[left:right]
        if not _whole_diff(selected):
            result.append(row)
            continue
        key = (metadata['session_id'], selected)
        if key in seen:
            continue
        seen.add(key)
        start = metadata['characters'][0]
        bounds = [start + left, start + right]
        meta = {**metadata, 'characters': bounds,
                'content_projection': 'historical_complete_diff'}
        identity = hashlib.sha256(json.dumps([meta['source_id'], *bounds], separators=(',', ':')).encode()).hexdigest()[:36]
        result.append({**row, 'id': 'span_' + identity,
                       'content': '[source ' + json.dumps(meta, ensure_ascii=False, separators=(',', ':')) + ']\n' + selected})
    return result
