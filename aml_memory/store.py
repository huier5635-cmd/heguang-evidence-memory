import hashlib
import heapq
import json
import sqlite3
import time
from pathlib import Path

import numpy as np

from .text import terms


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def grams(text: str) -> list[str]:
    out = []
    for word in terms(text, filter_stop=True):
        out.extend(word[i:i + 3] for i in range(max(1, len(word) - 2)))
    return out


class Store:
    """One database per user: content, lexical statistics, and vectors never cross users."""

    def __init__(self, root: Path, signature: str):
        self.root = root
        self.signature = signature
        root.mkdir(parents=True, exist_ok=True)

    def path(self, user_id):
        return self.root / (hashlib.sha256(user_id.encode()).hexdigest() + ".sqlite3")

    def connect(self, user_id, create=True):
        if sqlite3.sqlite_version_info < (3, 43, 0):
            raise RuntimeError("SQLite >= 3.43 is required for contentless-delete FTS5")
        path = self.path(user_id)
        if not create and not path.exists():
            return None
        db = sqlite3.connect(path, timeout=60)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA secure_delete=ON")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS writes (request_id TEXT PRIMARY KEY, digest TEXT NOT NULL, added_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS messages (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT, session_id TEXT,
                content TEXT, text TEXT, role TEXT, timestamp INTEGER, added_at REAL
            );
            CREATE TABLE IF NOT EXISTS docs (
                doc_rowid INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL,
                request_id TEXT, session_id TEXT, start_seq INTEGER,
                end_seq INTEGER, content TEXT, text TEXT, vector BLOB, added_at REAL
            );
            CREATE INDEX IF NOT EXISTS docs_request ON docs(request_id);
            CREATE INDEX IF NOT EXISTS docs_lookup ON docs(doc_rowid, id);
            CREATE INDEX IF NOT EXISTS messages_session ON messages(session_id, seq);
            CREATE VIRTUAL TABLE IF NOT EXISTS words USING fts5(text, content='', contentless_delete=1);
            CREATE VIRTUAL TABLE IF NOT EXISTS trigrams USING fts5(text, content='', contentless_delete=1);
        """)
        row = db.execute("SELECT value FROM meta WHERE key='signature'").fetchone()
        if row and row[0] != self.signature:
            db.close()
            raise RuntimeError("Stored index belongs to a different configuration; use a new data directory")
        db.execute("INSERT OR IGNORE INTO meta VALUES ('signature', ?)", (self.signature,))
        db.commit()
        return db

    def check_write(self, user_id, request_id, digest):
        db = self.connect(user_id)
        try:
            row = db.execute("SELECT digest FROM writes WHERE request_id=?", (request_id,)).fetchone()
            if row and row[0] != digest:
                raise ValueError("request_id was already committed with a different payload")
            return row is not None
        finally:
            db.close()

    def commit(self, req, digest, docs):
        db = self.connect(req.user_id)
        now = time.time()
        try:
            with db:
                # A transaction makes the entire Add searchable atomically.
                for message in req.messages:
                    db.execute("INSERT INTO messages(request_id,session_id,content,text,role,timestamp,added_at) VALUES(?,?,?,?,?,?,?)", (
                        req.request_id, req.session_id, json.dumps(message.model_dump()["content"], ensure_ascii=False),
                        None, message.role, message.timestamp, now))
                for doc in docs:
                    cursor = db.execute("""INSERT INTO docs(
                        id, request_id, session_id, start_seq, end_seq, content, text, vector, added_at
                    ) VALUES(?,?,?,?,?,?,?,?,?)""", (
                        doc["id"], req.request_id, req.session_id, doc["start"], doc["end"],
                        json.dumps(doc["content"], ensure_ascii=False),
                        None if isinstance(doc["content"], str) and doc["text"] == doc["content"] else doc["text"],
                        doc.get("vector"), now))
                    rowid = cursor.lastrowid
                    db.execute("INSERT INTO words(rowid,text) VALUES(?,?)", (rowid, " ".join(terms(doc["index_text"]))))
                    db.execute("INSERT INTO trigrams(rowid,text) VALUES(?,?)", (rowid, " ".join(grams(doc["index_text"]))))
                db.execute("INSERT INTO writes VALUES(?,?,?)", (req.request_id, digest, now))
        finally:
            db.close()

    @staticmethod
    def lexical(db, query, table, limit):
        tokens = list(dict.fromkeys(terms(query, filter_stop=True) if table == "words" else grams(query)))
        # Bound expression complexity, never interpolate raw query syntax.
        tokens = tokens[:160]
        if not tokens:
            return []
        expression = " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)
        return [r[0] for r in db.execute(
            f"SELECT docs.id FROM {table} JOIN docs INDEXED BY docs_lookup ON docs.rowid={table}.rowid "
            f"WHERE {table} MATCH ? ORDER BY bm25({table}), docs.id LIMIT ?",
            (expression, limit))]

    def candidates(self, user_id, query, options, limit):
        db = self.connect(user_id, create=False)
        if db is None:
            return {}, []
        try:
            lists = [(self.lexical(db, query, "words", limit), 1.0),
                     (self.lexical(db, query, "trigrams", limit), 0.75)]
            # Options get their own lower-weight retrieval path, no answer selection.
            for option in options:
                lists.append((self.lexical(db, query + " " + option, "words", limit // 2), 0.2))
            ids = {key for ranking, _ in lists for key in ranking}
            if ids:
                docs = self.fetch_docs_db(db, ids)
            else:
                # No lexical evidence: preserve a deterministic, bounded fallback.
                docs = {r["id"]: self.materialize_doc(r) for r in db.execute("SELECT * FROM docs ORDER BY id LIMIT ?", (limit,))}
            return docs, lists
        finally:
            db.close()

    @staticmethod
    def materialize_doc(row):
        """Restore only an omitted exact copy; media/distinct renderings stay stored."""
        doc = dict(row)
        if doc["text"] is None:
            try:
                text = json.loads(doc["content"])
            except (TypeError, ValueError):
                raise RuntimeError("Invalid stored source representation") from None
            if not isinstance(text, str):
                raise RuntimeError("Invalid stored source representation")
            doc["text"] = text
        return doc

    @staticmethod
    def fetch_docs_db(db, ids):
        ids = sorted(set(ids))
        docs = {}
        for offset in range(0, len(ids), 400):
            batch = ids[offset:offset + 400]
            placeholders = ",".join("?" for _ in batch)
            docs.update((r["id"], Store.materialize_doc(r)) for r in db.execute(
                f"SELECT * FROM docs WHERE id IN ({placeholders})", batch))
        return docs

    def fetch_docs(self, user_id, ids):
        db = self.connect(user_id, create=False)
        if db is None:
            return {}
        try:
            return self.fetch_docs_db(db, ids)
        finally:
            db.close()

    def dense_top(self, user_id, query_vector, limit, batch_size=256):
        """Exact cosine scan with bounded matrix/heap memory; never loads source bodies."""
        db = self.connect(user_id, create=False)
        if db is None:
            return []
        try:
            cursor = db.execute("SELECT id, vector FROM docs WHERE vector IS NOT NULL")
            def scored():
                while rows := cursor.fetchmany(batch_size):
                    vectors = [np.frombuffer(row["vector"], dtype=np.float32) for row in rows]
                    if any(v.shape != query_vector.shape or not np.isfinite(v).all() for v in vectors):
                        raise RuntimeError("Invalid stored vector shape or values")
                    similarities = np.stack(vectors) @ query_vector
                    for row, score in zip(rows, similarities, strict=True):
                        yield (float(score), row["id"])
            best = heapq.nsmallest(limit, scored(), key=lambda item: (-item[0], item[1]))
            return [key for _, key in best]
        finally:
            db.close()

    def purge_expired(self, days):
        """Delete expired writes even in mixed-age streaming user stores."""
        cutoff = time.time() - days * 86400
        for path in self.root.glob("*.sqlite3"):
            db = sqlite3.connect(path, timeout=60)
            try:
                db.execute("PRAGMA secure_delete=ON")
                old = [r[0] for r in db.execute("SELECT rowid FROM docs WHERE added_at < ?", (cutoff,))]
                with db:
                    db.executemany("DELETE FROM words WHERE rowid=?", [(x,) for x in old])
                    db.executemany("DELETE FROM trigrams WHERE rowid=?", [(x,) for x in old])
                    for table in ("docs", "messages", "writes"):
                        db.execute(f"DELETE FROM {table} WHERE added_at < ?", (cutoff,))
                if old:
                    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    db.execute("VACUUM")
            finally:
                db.close()

