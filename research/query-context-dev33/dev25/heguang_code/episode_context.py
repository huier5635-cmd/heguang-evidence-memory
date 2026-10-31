"""Pair complete historical problems with the same session's linked changes."""
import json

from .history_links import WIDTH
from .selection import MAX_SPAN_CHARACTERS, source_record
from .task_projection import project_task_descriptions


def _record(source, text, score):
    meta = {'source_id': source['id'], 'session_id': source['session_id'],
            'role': source['role'], 'characters': [0, len(text)],
            'evidence_kind': 'stored_user_message', 'current_task_verified': False}
    if source['timestamp'] is not None:
        meta['timestamp_ms'] = source['timestamp']
    if source['representation'] != 'content':
        meta['representation'] = source['representation']
    return {'id': source['id'], 'score': score,
            'content': '[source ' + json.dumps(meta, ensure_ascii=False, separators=(',', ':')) + ']\n' + text}


def _tag(row, field, problem_source):
    meta, body = source_record(row)
    meta = {**meta, 'episode_component': field, 'historical_problem_source_id': problem_source,
            'current_task_verified': False}
    return {**row, 'content': '[source ' + json.dumps(meta, ensure_ascii=False,
             separators=(',', ':')) + ']\n' + body}


def pair_historical_problems(store, req, ranked, rows):
    if (len(ranked) > 100 or len(rows) > 104
            or sum(len(r['content']) for r in ranked) > 512000
            or sum(len(r['content']) for r in rows) > 600000):
        raise RuntimeError('Historical episode candidate limit exceeded')
    db = store.connect(req.user_id, create=False)
    if db is None:
        return rows
    try:
        anchors = {}
        for row in ranked[:6]:
            meta, _ = source_record(row)
            source = db.execute('SELECT session_id FROM sources WHERE id=?', (meta['source_id'],)).fetchone()
            if source is not None and source['session_id'] == meta['session_id']:
                anchors.setdefault(meta['session_id'], row['score'])
            if len(anchors) == 2:
                break
        parsed = [(source_record(row)[0], row) for row in rows]
        priority = []
        for session, score in anchors.items():
            problem = db.execute("""SELECT * FROM sources WHERE session_id=? AND role='user'
                ORDER BY added_at,rowid LIMIT 1""", (session,)).fetchone()
            if problem is None or not 0 < problem['chars'] <= MAX_SPAN_CHARACTERS:
                continue
            body = store.source_span(db, problem['id'], 0, problem['chars'], {})
            description = project_task_descriptions([_record(problem, body, score)])[0]
            meta, text = source_record(description)
            # Preserve the complete description, including its constraints. A
            # long or unfamiliar source keeps the parent's unchanged fallback.
            if meta.get('content_projection') != 'historical_pr_description' or len(text) > 4096:
                continue
            latest = db.execute("""SELECT s.id FROM sources s WHERE s.session_id=?
                AND EXISTS(SELECT 1 FROM diff_spans d WHERE d.source_id=s.id)
                ORDER BY s.added_at DESC,s.rowid DESC LIMIT 1""", (session,)).fetchone()
            if latest is None:
                continue
            left, right = db.execute('SELECT min(start),max(end) FROM diff_spans WHERE source_id=?', (latest['id'],)).fetchone()
            bounds = [(left, right)] if right-left <= WIDTH else [(left, left+WIDTH), (right-WIDTH, right)]
            changes = []
            for a, b in bounds:
                match = next((row for m, row in parsed if m['source_id'] == latest['id']
                              and m['session_id'] == session and m['characters'] == [a, b]), None)
                if match is None:
                    break
                changes.append(match)
            if len(changes) != len(bounds):
                continue
            # These are separate original source records, never a fabricated
            # composite span. Group membership is explicit metadata only.
            priority.append(_tag(description, 'historical_problem', problem['id']))
            priority.extend(_tag(row, 'historical_change', problem['id']) for row in changes)
        if not priority:
            return rows
        result, seen, used = [], set(), 0
        for row in [*priority, *rows]:
            meta, _ = source_record(row)
            identity = (meta['source_id'], *meta['characters'])
            if identity in seen or used + len(row['content']) > 600000:
                continue
            seen.add(identity)
            # A grouped episode changes output order. Publish ordinal scores
            # consistent with that order rather than stale pre-group ranks.
            result.append({**row, 'score': 1. / (61 + len(result))})
            used += len(row['content'])
            if len(result) == 100:
                break
        return result
    finally:
        db.close()
