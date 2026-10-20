import hashlib
import json
import sqlite3
import time
from pathlib import Path

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
                id TEXT PRIMARY KEY, request_id TEXT, session_id TEXT, start_seq INTEGER,
                end_seq INTEGER, content TEXT, text TEXT, vector BLOB, added_at REAL
            );
            CREATE INDEX IF NOT EXISTS docs_request ON docs(request_id);
            CREATE INDEX IF NOT EXISTS messages_session ON messages(session_id, seq);
            CREATE VIRTUAL TABLE IF NOT EXISTS words USING fts5(id UNINDEXED, text);
            CREATE VIRTUAL TABLE IF NOT EXISTS trigrams USING fts5(id UNINDEXED, text);
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
                    from .schema import content_text
                    db.execute("INSERT INTO messages(request_id,session_id,content,text,role,timestamp,added_at) VALUES(?,?,?,?,?,?,?)", (
                        req.request_id, req.session_id, json.dumps(message.model_dump()["content"], ensure_ascii=False),
                        content_text(message.content), message.role, message.timestamp, now))
                for doc in docs:
                    db.execute("INSERT INTO docs VALUES(?,?,?,?,?,?,?,?,?)", (
                        doc["id"], req.request_id, req.session_id, doc["start"], doc["end"],
                        json.dumps(doc["content"], ensure_ascii=False), doc["text"], doc.get("vector"), now))
                    db.execute("INSERT INTO words VALUES(?,?)", (doc["id"], " ".join(terms(doc["index_text"]))))
                    db.execute("INSERT INTO trigrams VALUES(?,?)", (doc["id"], " ".join(grams(doc["index_text"]))))
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
            f"SELECT id FROM {table} WHERE {table} MATCH ? ORDER BY bm25({table}), id LIMIT ?",
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
            docs = {r["id"]: dict(r) for r in db.execute("SELECT * FROM docs")}
            return docs, lists
        finally:
            db.close()

    def purge_expired(self, days):
        """Delete expired writes even in mixed-age streaming user stores."""
        cutoff = time.time() - days * 86400
        for path in self.root.glob("*.sqlite3"):
            db = sqlite3.connect(path, timeout=60)
            try:
                db.execute("PRAGMA secure_delete=ON")
                old = [r[0] for r in db.execute("SELECT id FROM docs WHERE added_at < ?", (cutoff,))]
                with db:
                    db.executemany("DELETE FROM words WHERE id=?", [(x,) for x in old])
                    db.executemany("DELETE FROM trigrams WHERE id=?", [(x,) for x in old])
                    for table in ("docs", "messages", "writes"):
                        db.execute(f"DELETE FROM {table} WHERE added_at < ?", (cutoff,))
                if old:
                    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    db.execute("VACUUM")
            finally:
                db.close()

