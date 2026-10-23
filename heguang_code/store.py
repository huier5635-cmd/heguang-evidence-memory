import hashlib
import heapq
import json
import math
import sqlite3
import time
import zlib
from collections import defaultdict

from .features import chunks, encoded, technical, words
from .feature_storage import decode_features, encode_features
from .normalize import PAIR_COMMAND_LIMIT, commands_in, trajectory_views


class ConflictingWrite(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def streaming_digest(value):
    """Same canonical digest without a second request-sized UTF-8 buffer."""
    checksum = hashlib.sha256()
    encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    for piece in encoder.iterencode(value):
        for start in range(0, len(piece), 16384):
            checksum.update(piece[start:start + 16384].encode())
    return checksum.hexdigest()


class Store:
    def __init__(self, settings):
        self.settings = settings
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        # Only stored representation/tokenization belongs in the index signature.
        # Search budgets and reranking can be compared on exactly the same index.
        self.signature = digest({"schema": 5, "source_view": 4, "feature_codec": 1,
                                 "rich_tokens": settings.profile != "baseline",
                                 "chunk_chars": settings.chunk_chars,
                                 "overlap_chars": settings.overlap_chars,
                                 "block_chars": settings.block_chars})

    def path(self, user):
        return self.settings.data_dir / (hashlib.sha256(user.encode()).hexdigest() + ".sqlite3")

    def connect(self, user, create=True):
        path = self.path(user)
        if not create and not path.exists():
            return None
        if sqlite3.sqlite_version_info < (3, 43, 0):
            raise RuntimeError("SQLite 3.43+ is required")
        db = sqlite3.connect(path, timeout=60)
        db.row_factory = sqlite3.Row
        try:
            if not create:
                db.execute("PRAGMA query_only=ON")
                row = db.execute("SELECT value FROM meta WHERE key='signature'").fetchone()
                if row is None or row[0] != self.signature:
                    raise RuntimeError("Configuration mismatch; use a fresh data directory")
                return db
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA secure_delete=ON")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS writes(request_id TEXT PRIMARY KEY,digest TEXT NOT NULL,added_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS sources(
                    id TEXT PRIMARY KEY,request_id TEXT NOT NULL,session_id TEXT NOT NULL,
                    role TEXT NOT NULL,timestamp INTEGER,chars INTEGER NOT NULL,
                    representation TEXT NOT NULL,paired_source_id TEXT REFERENCES sources(id) ON DELETE SET NULL,added_at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS sources_request ON sources(request_id);
                CREATE TABLE IF NOT EXISTS raw_messages(
                    source_id TEXT PRIMARY KEY REFERENCES sources(id) ON DELETE CASCADE,
                    body BLOB NOT NULL,digest TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS call_state(
                    session_id TEXT NOT NULL,tool_id TEXT NOT NULL,
                    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                    command BLOB NOT NULL,digest TEXT NOT NULL,ambiguous INTEGER NOT NULL,
                    PRIMARY KEY(session_id,tool_id));
                CREATE TABLE IF NOT EXISTS blobs(hash TEXT PRIMARY KEY,body BLOB NOT NULL,chars INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS blocks(
                    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                    part INTEGER NOT NULL,start INTEGER NOT NULL,end INTEGER NOT NULL,
                    hash TEXT NOT NULL REFERENCES blobs(hash),PRIMARY KEY(source_id,part));
                CREATE TABLE IF NOT EXISTS docs(
                    rowid INTEGER PRIMARY KEY,id TEXT UNIQUE NOT NULL,
                    paths TEXT NOT NULL,symbols TEXT NOT NULL,word_count INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS spans(
                    doc_rowid INTEGER NOT NULL REFERENCES docs(rowid) ON DELETE CASCADE,
                    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                    start INTEGER NOT NULL,end INTEGER NOT NULL,
                    PRIMARY KEY(doc_rowid,source_id,start));
                CREATE INDEX IF NOT EXISTS spans_source ON spans(source_id);
                CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(
                    body,symbols,paths,content='',contentless_delete=1,
                    tokenize='unicode61 remove_diacritics 0');
                CREATE VIRTUAL TABLE IF NOT EXISTS vocabulary USING fts5vocab(search,'col');
            """)
            row = db.execute("SELECT value FROM meta WHERE key='signature'").fetchone()
            if row and row[0] != self.signature:
                raise RuntimeError("Configuration mismatch; use a fresh data directory")
            db.execute("INSERT OR IGNORE INTO meta VALUES('signature',?)", (self.signature,))
            db.commit()
            return db
        except BaseException:
            db.close()
            raise

    def add(self, req):
        checksum = streaming_digest(req.model_dump())
        db = self.connect(req.user_id)
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT digest FROM writes WHERE request_id=?", (req.request_id,)).fetchone()
            if row:
                if row[0] != checksum:
                    raise ConflictingWrite("request_id already committed with a different payload")
                db.rollback()
                return
            added_at = time.time()
            identities = set()
            for message in req.messages:
                identities.update(tool_id for tool_id, _ in commands_in(message))
                tool_id = message.model_extra.get("tool_call_id")
                if message.role == "tool" and isinstance(tool_id, str):
                    identities.add(tool_id)
            preceding, ambiguous = {}, set()
            identities = sorted(identities)
            for start in range(0, len(identities), 400):
                batch = identities[start:start + 400]
                placeholders = ",".join("?" for _ in batch)
                rows = db.execute(f"SELECT * FROM call_state WHERE session_id=? AND tool_id IN ({placeholders})", [req.session_id, *batch])
                for row in rows:
                    decoder = zlib.decompressobj()
                    try:
                        raw = decoder.decompress(row["command"], PAIR_COMMAND_LIMIT * 4 + 1)
                    except zlib.error:
                        raise RuntimeError("Corrupt command compression") from None
                    if not decoder.eof or decoder.unused_data or len(raw) > PAIR_COMMAND_LIMIT * 4 or hashlib.sha256(raw).hexdigest() != row["digest"]:
                        raise RuntimeError("Corrupt command state")
                    try:
                        command = raw.decode()
                    except UnicodeDecodeError:
                        raise RuntimeError("Corrupt command encoding") from None
                    if len(command) > PAIR_COMMAND_LIMIT:
                        raise RuntimeError("Corrupt command length")
                    preceding[row["tool_id"]] = (row["source_id"], command)
                    if row["ambiguous"]:
                        ambiguous.add(row["tool_id"])
            views = trajectory_views(req.messages, preceding, ambiguous)
            for position, (message, view) in enumerate(zip(req.messages, views, strict=True)):
                source_id = digest([req.user_id, req.request_id, position])[:40]
                text, representation, paired_position = view
                paired_source = (paired_position if isinstance(paired_position, str) else
                                 None if paired_position is None else digest([req.user_id, req.request_id, paired_position])[:40])
                db.execute("INSERT INTO sources(id,request_id,session_id,role,timestamp,chars,representation,paired_source_id,added_at) VALUES(?,?,?,?,?,?,?,?,?)", (
                    source_id, req.request_id, req.session_id, message.role, message.timestamp, len(text), representation, paired_source, added_at))
                original = message.model_dump(exclude_unset=True)
                if set(original) - {"role", "content", "timestamp"}:
                    raw = json.dumps(original, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
                    db.execute("INSERT INTO raw_messages VALUES(?,?,?)", (source_id, zlib.compress(raw, 3), hashlib.sha256(raw).hexdigest()))
                block_size = self.settings.block_chars
                for part, start in enumerate(range(0, len(text), block_size)):
                    block = text[start:start + block_size]
                    raw = block.encode()
                    checksum_block = hashlib.sha256(raw).hexdigest()
                    db.execute("INSERT OR IGNORE INTO blobs VALUES(?,?,?)", (checksum_block, zlib.compress(raw, 3), len(block)))
                    db.execute("INSERT INTO blocks VALUES(?,?,?,?,?)", (source_id, part, start, start + len(block), checksum_block))
                for start, end, chunk in chunks(text, self.settings.chunk_chars, self.settings.overlap_chars):
                    # Identical source spans are indexed once inside this user's database.
                    doc_id = "code_" + digest([req.user_id, chunk])[:36]
                    found = db.execute("SELECT rowid FROM docs WHERE id=?", (doc_id,)).fetchone()
                    if found:
                        rowid = found[0]
                    else:
                        paths, symbols = technical(chunk)
                        body = words(chunk, split=self.settings.profile != "baseline", stop=self.settings.profile != "baseline")
                        cursor = db.execute("INSERT INTO docs(id,paths,symbols,word_count) VALUES(?,?,?,?)", (
                            doc_id, encode_features(paths), encode_features(symbols), len(body)))
                        rowid = cursor.lastrowid
                        db.execute("INSERT INTO search(rowid,body,symbols,paths) VALUES(?,?,?,?)", (
                            rowid, " ".join(body), " ".join(encoded(symbols, "s")), " ".join(encoded(paths, "p"))))
                    db.execute("INSERT INTO spans VALUES(?,?,?,?)", (rowid, source_id, start, end))
            for tool_id, (parent, command) in preceding.items():
                if isinstance(parent, int):
                    raw = command.encode()
                    db.execute("""INSERT INTO call_state VALUES(?,?,?,?,?,?) ON CONFLICT(session_id,tool_id)
                               DO UPDATE SET source_id=excluded.source_id,command=excluded.command,
                               digest=excluded.digest,ambiguous=excluded.ambiguous""", (
                               req.session_id, tool_id, digest([req.user_id, req.request_id, parent])[:40],
                               zlib.compress(raw, 3), hashlib.sha256(raw).hexdigest(), int(tool_id in ambiguous)))
            db.execute("INSERT INTO writes VALUES(?,?,?)", (req.request_id, checksum, added_at))
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def ranking(db, field, tokens, limit):
        if not tokens:
            return []
        expression = field + ":(" + " OR ".join('"' + value.replace('"', '""') + '"' for value in tokens) + ")"
        return [row[0] for row in db.execute(
            "SELECT rowid FROM search WHERE search MATCH ? ORDER BY bm25(search),rowid LIMIT ?", (expression, limit))]

    def query_terms(self, db, query):
        tokens = list(dict.fromkeys(words(query, split=self.settings.profile != "baseline", stop=self.settings.profile != "baseline")))
        if self.settings.profile == "baseline":
            return tokens[:120]
        frequency = {}
        for start in range(0, len(tokens), 400):
            batch = tokens[start:start + 400]
            placeholders = ",".join("?" for _ in batch)
            for row in db.execute(f"SELECT term,doc FROM vocabulary WHERE col='body' AND term IN ({placeholders})", batch):
                frequency[row[0]] = row[1]
        # Stable rarity selection includes technical cues near the end of long queries.
        return sorted((token for token in tokens if token in frequency), key=lambda token: (frequency[token], -len(token), token))[:120]

    @staticmethod
    def feature_rarity(db, field, values, prefix):
        values = sorted(values)
        tokens = encoded(values, prefix)
        if not tokens:
            return {}
        total = db.execute("SELECT count(*) FROM docs").fetchone()[0]
        frequency = {}
        for start in range(0, len(tokens), 400):
            batch = tokens[start:start + 400]
            placeholders = ",".join("?" for _ in batch)
            for row in db.execute(f"SELECT term,doc FROM vocabulary WHERE col=? AND term IN ({placeholders})", [field, *batch]):
                frequency[row[0]] = row[1]
        return {value: math.log1p(total / (frequency[token] + 1)) / math.log1p(max(2, total))
                for value, token in zip(values, tokens) if token in frequency}

    def source_span(self, db, source_id, start, end, cache):
        out, cursor = [], start
        for row in db.execute("""SELECT blocks.start,blocks.end,blobs.hash,blobs.body,blobs.chars FROM blocks
                               JOIN blobs USING(hash) WHERE source_id=? AND blocks.end>? AND blocks.start<? ORDER BY part""",
                              (source_id, start, end)):
            key = row["hash"]
            if key not in cache:
                decoder = zlib.decompressobj()
                try:
                    raw = decoder.decompress(row["body"], self.settings.block_chars * 4 + 1)
                except zlib.error:
                    raise RuntimeError("Corrupt source compression") from None
                if not decoder.eof or decoder.unused_data or len(raw) > self.settings.block_chars * 4:
                    raise RuntimeError("Corrupt source block")
                if hashlib.sha256(raw).hexdigest() != key:
                    raise RuntimeError("Corrupt source digest")
                try:
                    text = raw.decode()
                except UnicodeDecodeError:
                    raise RuntimeError("Corrupt source encoding") from None
                if len(text) != row["chars"]:
                    raise RuntimeError("Corrupt source length")
                # The return budget bounds output; cache eviction additionally bounds reuse.
                if len(cache) >= 256:
                    cache.clear()
                cache[key] = text
            if row["start"] > cursor:
                raise RuntimeError("Missing source block")
            right = min(end, row["end"])
            out.append(cache[key][cursor - row["start"]:right - row["start"]])
            cursor = right
        if cursor != end:
            raise RuntimeError("Incomplete source span")
        return "".join(out)

    def search(self, req, *, selection_pool=False):
        # A bounded internal pool lets a later selector see candidates before
        # the final evidence budget. The public no-model path stays unchanged.
        character_limit = self.settings.response_chars
        if selection_pool:
            character_limit = max(character_limit, min(512000, req.top_k * (self.settings.chunk_chars + 1024)))
        db = self.connect(req.user_id, create=False)
        if db is None:
            return []
        try:
            paths, symbols = technical(req.query)
            lists = [(self.ranking(db, "body", self.query_terms(db, req.query), self.settings.candidates), 1.0)]
            if self.settings.profile != "baseline":
                path_rarity = self.feature_rarity(db, "paths", paths, "p") if self.settings.profile == "rarity" else dict.fromkeys(paths, 1.0)
                symbol_rarity = self.feature_rarity(db, "symbols", symbols, "s") if self.settings.profile == "rarity" else dict.fromkeys(symbols, 1.0)
                lists += [(self.ranking(db, "paths", encoded(paths, "p")[:80], self.settings.candidates), 1.4),
                          (self.ranking(db, "symbols", encoded(symbols, "s")[:100], self.settings.candidates), 0.8)]
                lists[1] = (lists[1][0], lists[1][1] * max(path_rarity.values(), default=0))
                lists[2] = (lists[2][0], lists[2][1] * max(symbol_rarity.values(), default=0))
            scores = defaultdict(float)
            for ranking, weight in lists:
                for index, rowid in enumerate(ranking):
                    scores[rowid] += weight / (60 + index + 1)
            if not scores:
                return []
            documents = {}
            rowids = sorted(scores)
            for offset in range(0, len(rowids), 400):
                batch = rowids[offset:offset + 400]
                placeholders = ",".join("?" for _ in batch)
                documents.update((row["rowid"], dict(row)) for row in db.execute(f"SELECT rowid,* FROM docs WHERE rowid IN ({placeholders})", batch))
            if self.settings.profile != "baseline":
                for rowid, doc in documents.items():
                    path_hits = sum(path_rarity.get(value, 0) for value in decode_features(doc["paths"]))
                    symbol_hits = sum(symbol_rarity.get(value, 0) for value in decode_features(doc["symbols"]))
                    scores[rowid] += min(0.018, path_hits * 0.006) + min(0.012, symbol_hits * 0.003)
            pending = [(-score, documents[rowid]["id"], rowid) for rowid, score in scores.items()]
            heapq.heapify(pending)
            result, used_chars, cache = [], 0, {}
            covered = defaultdict(list)
            session_counts, provenance = defaultdict(int), {}
            while pending:
                previous, _, rowid = heapq.heappop(pending)
                if rowid not in provenance:
                    provenance[rowid] = db.execute("""SELECT spans.*,sources.session_id,sources.role,sources.timestamp,sources.representation,sources.paired_source_id FROM spans
                                  JOIN sources ON sources.id=spans.source_id WHERE doc_rowid=?
                                  ORDER BY source_id,start LIMIT 1""", (rowid,)).fetchone()
                span = provenance[rowid]
                if span is None:
                    raise RuntimeError("Missing document provenance")
                score = scores[rowid] / (1 + self.settings.session_penalty * session_counts[span["session_id"]])
                # Lazy heap updates reconsider a repeated session against the
                # remaining sessions. Every update only lowers its score.
                if abs(score + previous) > 1e-12:
                    heapq.heappush(pending, (-score, documents[rowid]["id"], rowid))
                    continue
                intervals = covered[span["source_id"]]
                overlap = sum(max(0, min(span["end"], end) - max(span["start"], start)) for start, end in intervals)
                if overlap > 0.8 * (span["end"] - span["start"]):
                    continue
                header = {"source_id": span["source_id"], "session_id": span["session_id"],
                          "role": span["role"], "characters": [span["start"], span["end"]]}
                if span["timestamp"] is not None:
                    header["timestamp_ms"] = span["timestamp"]
                if span["representation"] != "content":
                    header["representation"] = span["representation"]
                if span["paired_source_id"] is not None:
                    header["paired_source_id"] = span["paired_source_id"]
                elif span["representation"] == "trajectory_step_v1":
                    header["paired_parent_expired"] = True
                prefix = "[source " + json.dumps(header, ensure_ascii=False, separators=(",", ":")) + "]\n"
                if used_chars + len(prefix) + span["end"] - span["start"] > character_limit:
                    continue
                content = prefix + self.source_span(db, span["source_id"], span["start"], span["end"], cache)
                result.append({"id": documents[rowid]["id"], "content": content, "score": score})
                used_chars += len(content)
                intervals.append((span["start"], span["end"]))
                session_counts[span["session_id"]] += 1
                if len(result) >= req.top_k:
                    break
            return result
        finally:
            db.close()

    def purge_expired(self):
        cutoff = time.time() - self.settings.retention_days * 86400
        for path in self.settings.data_dir.glob("*.sqlite3"):
            db = sqlite3.connect(path, timeout=60)
            try:
                db.execute("PRAGMA foreign_keys=ON")
                db.execute("PRAGMA secure_delete=ON")
                with db:
                    deleted = db.execute("DELETE FROM sources WHERE added_at<?", (cutoff,)).rowcount
                    db.execute("DELETE FROM writes WHERE added_at<?", (cutoff,))
                    if deleted:
                        orphaned = [row[0] for row in db.execute("SELECT rowid FROM docs WHERE NOT EXISTS(SELECT 1 FROM spans WHERE doc_rowid=docs.rowid)")]
                        db.executemany("DELETE FROM search WHERE rowid=?", [(rowid,) for rowid in orphaned])
                        db.executemany("DELETE FROM docs WHERE rowid=?", [(rowid,) for rowid in orphaned])
                        db.execute("DELETE FROM blobs WHERE NOT EXISTS(SELECT 1 FROM blocks WHERE blocks.hash=blobs.hash)")
                if deleted:
                    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                    db.execute("VACUUM")
            finally:
                db.close()
