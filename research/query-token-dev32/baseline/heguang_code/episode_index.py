"""Retrieve historical problems independently of verbose trajectory chunks.

Only tokens are indexed here. Returned text is reconstructed and checked from
the same per-user original-source blocks used by the passage index.
"""
from collections import defaultdict
import hashlib
import json

from .features import encoded, technical, words
from .selection import MAX_SPAN_CHARACTERS, source_record
from .task_projection import project_task_descriptions
from .query_focus import focus_query

MAX_PROBLEM_CHARACTERS = 8192


def initialize(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS episode_docs(
            rowid INTEGER PRIMARY KEY,
            source_id TEXT UNIQUE NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
            session_id TEXT UNIQUE NOT NULL,
            start INTEGER NOT NULL,end INTEGER NOT NULL);
        CREATE VIRTUAL TABLE IF NOT EXISTS episode_search USING fts5(
            body,symbols,paths,content='',contentless_delete=1,
            tokenize='unicode61 remove_diacritics 0');
        CREATE VIRTUAL TABLE IF NOT EXISTS episode_vocabulary USING fts5vocab(episode_search,'col');
    """)


def _record(source, text, start=0, score=0.0):
    meta = {'source_id': source['id'], 'session_id': source['session_id'],
            'role': source['role'], 'characters': [start, start + len(text)]}
    if source['timestamp'] is not None:
        meta['timestamp_ms'] = source['timestamp']
    if source['representation'] != 'content':
        meta['representation'] = source['representation']
    identity = hashlib.sha256(json.dumps([source['id'], start, start + len(text)],
                                separators=(',', ':')).encode()).hexdigest()[:36]
    return {'id': 'span_' + identity, 'score': score,
            'content': '[source ' + json.dumps(meta, ensure_ascii=False,
                         separators=(',', ':')) + ']\n' + text}


def refresh_session(store, db, session):
    """Run inside Add's existing transaction; never create a second commit."""
    if db.execute('SELECT 1 FROM episode_docs WHERE session_id=?', (session,)).fetchone():
        return
    first = db.execute("""SELECT * FROM sources WHERE session_id=? AND role='user'
                          ORDER BY added_at,rowid LIMIT 1""", (session,)).fetchone()
    if first is None or not 0 < first['chars'] <= MAX_SPAN_CHARACTERS:
        return
    text = store.source_span(db, first['id'], 0, first['chars'], {})
    row = project_task_descriptions([_record(first, text)])[0]
    meta, problem = source_record(row)
    # An incomplete/unrecognized workflow wrapper is not a task summary.
    if (text.startswith('<pr_description>')
            and meta.get('content_projection') != 'historical_pr_description'):
        return
    if not problem.strip() or len(problem) > MAX_PROBLEM_CHARACTERS:
        return
    paths, symbols = technical(problem)
    body = words(problem, split=store.settings.profile != 'baseline',
                 stop=store.settings.profile != 'baseline')
    cursor = db.execute('INSERT INTO episode_docs(source_id,session_id,start,end) VALUES(?,?,?,?)',
                        (first['id'], session, *meta['characters']))
    db.execute('INSERT INTO episode_search(rowid,body,symbols,paths) VALUES(?,?,?,?)',
                (cursor.lastrowid, ' '.join(body), ' '.join(encoded(symbols, 's')),
                 ' '.join(encoded(paths, 'p'))))


def purge_orphans(db):
    # sources deletion cascades into episode_docs; the contentless FTS table
    # needs its own deletion so expired vocabulary cannot affect later ranks.
    orphaned = [r[0] for r in db.execute('''SELECT rowid FROM episode_search
        WHERE rowid NOT IN (SELECT rowid FROM episode_docs)''')]
    db.executemany('DELETE FROM episode_search WHERE rowid=?', [(v,) for v in orphaned])


def _ranking(db, field, tokens, limit):
    if not tokens:
        return []
    expression = field + ':(' + ' OR '.join('"' + v.replace('"', '""') + '"' for v in tokens) + ')'
    return [r[0] for r in db.execute('''SELECT rowid FROM episode_search
        WHERE episode_search MATCH ? ORDER BY bm25(episode_search),rowid LIMIT ?''',
                                    (expression, limit))]


def search_episodes(store, req, *, limit=8):
    if type(limit) is not int or not 1 <= limit <= 16:
        raise ValueError('Invalid episode candidate limit')
    db = store.connect(req.user_id, create=False)
    if db is None:
        return []
    try:
        def body_ranking(query):
            tokens = list(dict.fromkeys(words(query, split=store.settings.profile != 'baseline',
                                             stop=store.settings.profile != 'baseline')))
            frequency = {}
            for start in range(0, len(tokens), 400):
                batch = tokens[start:start + 400]
                placeholders = ','.join('?' for _ in batch)
                for token, count in db.execute(f'''SELECT term,doc FROM episode_vocabulary
                        WHERE col='body' AND term IN ({placeholders})''', batch):
                    frequency[token] = count
            selected = sorted((t for t in tokens if t in frequency),
                              key=lambda t: (frequency[t], -len(t), t))[:120]
            return _ranking(db, 'body', selected, 64)
        rankings = [(body_ranking(req.query), 1.0)]
        if store.settings.profile != 'baseline':
            paths, symbols = technical(req.query)
            rankings.extend([(_ranking(db, 'paths', encoded(paths, 'p')[:80], 64), 1.4),
                             (_ranking(db, 'symbols', encoded(symbols, 's')[:100], 64), .8)])
            focused = focus_query(req.query)
            if focused != req.query:
                rankings.append((body_ranking(focused), 1.0))
        scores = defaultdict(float)
        for ranking, weight in rankings:
            for rank, rowid in enumerate(ranking, 1):
                scores[rowid] += weight / (60 + rank)
        result, cache = [], {}
        for rowid in sorted(scores, key=lambda v: (-scores[v], v))[:limit]:
            source = db.execute('''SELECT s.*,e.start,e.end FROM episode_docs e
                JOIN sources s ON s.id=e.source_id WHERE e.rowid=?''', (rowid,)).fetchone()
            if source is None or not 0 < source['end'] - source['start'] <= MAX_PROBLEM_CHARACTERS:
                raise RuntimeError('Corrupt historical problem index')
            text = store.source_span(db, source['id'], source['start'], source['end'], cache)
            result.append(_record(source, text, source['start'], scores[rowid]))
        return result
    finally:
        db.close()


def blend_candidates(episodes, passages):
    """Reserve up to eight of the existing sixteen scorer pairs for problems.

    The initial order alternates the two rankings. Every selected problem is a
    complete original span; chunks from that same source are redundant and are
    removed before scoring/coalescing. No extra model calls or hidden summaries.
    """
    if (len(episodes) > 8 or len(passages) > 100
            or sum(len(r['content']) for r in episodes) > 100000
            or sum(len(r['content']) for r in passages) > 512000):
        raise RuntimeError('Episode blend candidate limit exceeded')
    episode_sources = {source_record(row)[0]['source_id'] for row in episodes}
    remaining = [row for row in passages if source_record(row)[0]['source_id'] not in episode_sources]
    output = []
    for rank in range(max(len(episodes), len(remaining))):
        if rank < len(episodes):
            output.append(episodes[rank])
        if rank < len(remaining):
            output.append(remaining[rank])
    # The normal Store pool can reach 512k. A few full problems must not make
    # the selector's existing hard bound fail; discard only the lowest tail.
    result, used = [], 0
    for row in output:
        if len(result) == 100:
            break
        if used + len(row['content']) > 512000:
            continue
        result.append({**row, 'score': 1. / (61 + len(result))})
        used += len(row['content'])
    return result
