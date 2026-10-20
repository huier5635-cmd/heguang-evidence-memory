import asyncio
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone

from .models import AcademicModels
from . import __version__
from .schema import content_text
from .store import Store, fingerprint
from .text import quoted_entities, split_spans


class Engine:
    def __init__(self, settings):
        self.settings = settings
        signature = fingerprint({"mode": settings.mode, "chunk": settings.chunk_chars,
                                 "overlap": settings.overlap_chars, "version": __version__,
                                 "window_stride": settings.window_stride,
                                 "compact_headers": settings.compact_headers,
                                 "embedding": AcademicModels.EMBEDDING_MODEL,
                                 "llm": AcademicModels.LLM_MODEL,
                                 "embedding_url": settings.embedding_url if settings.mode != "lexical" else "unused"})
        self.store = Store(settings.data_dir, signature)
        # Striped locks bound memory use; no evaluation content is stored in the lock map.
        self.locks = [asyncio.Lock() for _ in range(256)]
        self.models = AcademicModels(settings)

    def lock(self, user_id):
        return self.locks[int(fingerprint(user_id)[:8], 16) % len(self.locks)]

    def render(self, message, index, session_id):
        source = {"session_id": session_id, "message": index, "role": message.role}
        if message.timestamp is not None:
            source["timestamp_ms"] = message.timestamp
            try:
                source["utc"] = datetime.fromtimestamp(message.timestamp / 1000, timezone.utc).isoformat()
            except (ValueError, OSError, OverflowError):
                pass
        if self.settings.compact_headers:
            date = source.get("utc", source.get("timestamp_ms", ""))
            return f"[{fingerprint(session_id)[:10]}#{index} {message.role} {date}]\n" + content_text(message.content)
        return "[source " + json.dumps(source, ensure_ascii=False) + "]\n" + content_text(message.content)

    def documents(self, req):
        rendered = [self.render(m, i, req.session_id) for i, m in enumerate(req.messages)]
        docs = []
        covered_until = 0
        for i, message in enumerate(req.messages):
            if isinstance(message.content, list):
                parts = [{"type": "text", "text": "[source " + json.dumps({"session_id": req.session_id, "message": i, "role": message.role}) + "]"}]
                parts.extend(part.model_dump() for part in message.content)
                docs.append({"start": i, "end": i + 1, "text": rendered[i], "content": parts})
            elif len(rendered[i]) > self.settings.chunk_chars:
                for start, end, text in split_spans(content_text(message.content), self.settings.chunk_chars, self.settings.overlap_chars):
                    header = rendered[i].split("\n", 1)[0] + f" [characters {start}:{end}]\n"
                    docs.append({"start": i, "end": i + 1, "text": header + text, "content": header + text})
            elif i >= covered_until or (self.settings.window_stride == 2 and i % 2 == 0):
                # A four-turn source window preserves speaker/pronoun context.
                group = []
                for j in range(i, min(i + 4, len(rendered))):
                    if isinstance(req.messages[j].content, list) or len(rendered[j]) > self.settings.chunk_chars:
                        break
                    group.append(rendered[j])
                if group:
                    text = "\n\n".join(group)
                    docs.append({"start": i, "end": i + len(group), "text": text, "content": text})
                    covered_until = i + len(group)
        for index, doc in enumerate(docs):
            doc["id"] = "mem_" + fingerprint([req.user_id, req.request_id, index, doc["content"]])[:32]
            doc["index_text"] = doc["text"]
        return docs

    async def add(self, req):
        digest = fingerprint(req.model_dump())
        async with self.lock(req.user_id):
            if await asyncio.to_thread(self.store.check_write, req.user_id, req.request_id, digest):
                return
            docs = self.documents(req)
            if self.settings.mode == "research":
                source = "\n\n".join(self.render(message, index, req.session_id)
                                      for index, message in enumerate(req.messages))
                segments = list(split_spans(source, 16000, 0))
                tagged = await asyncio.gather(*(self.models.source_tags(text) for _, _, text in segments))
                entries = [entry for batch in tagged for entry in batch]
                for doc in docs:
                    tags = [tag for entry in entries if entry["quote"] in doc["text"] for tag in entry["tags"]]
                    doc["index_text"] += "\n" + " ".join(dict.fromkeys(tags))
            if self.settings.mode != "lexical":
                vectors = await self.models.embed([doc["index_text"] for doc in docs])
                for doc, vector in zip(docs, vectors, strict=True):
                    doc["vector"] = vector.tobytes()
            await asyncio.to_thread(self.store.commit, req, digest, docs)

    async def search(self, req):
        query = content_text(req.query)
        async with self.lock(req.user_id):
            docs, ranked_lists = await asyncio.to_thread(self.store.candidates, req.user_id, query, req.options, self.settings.candidate_limit)
            if not docs:
                return []
            if self.settings.mode != "lexical":
                query_vector = (await self.models.embed([query]))[0]
                dense = await asyncio.to_thread(self.store.dense_top, req.user_id, query_vector, self.settings.candidate_limit)
                docs.update(await asyncio.to_thread(self.store.fetch_docs, req.user_id, dense))
                ranked_lists.append((dense, 1.0))
            scores = defaultdict(float)
            for ranking, weight in ranked_lists:
                for rank, key in enumerate(ranking):
                    scores[key] += weight / (60 + rank + 1)
            # Bounded pseudo-relevance feedback uses named strings found in sources only.
            if scores and self.settings.bridge_weight > 0:
                seeds = sorted(scores, key=lambda key: (-scores[key], key))[:4]
                names = Counter(x for key in seeds for x in quoted_entities(docs[key]["text"].split("]\n", 1)[-1]))
                extra = [name for name, count in names.most_common(8) if count >= 2 and name.lower() not in query.lower()]
                if extra:
                    bridge_docs, bridge_lists = await asyncio.to_thread(self.store.candidates, req.user_id, query + " " + " ".join(extra), [], self.settings.candidate_limit // 2)
                    docs.update(bridge_docs)
                    for ranking, weight in bridge_lists:
                        for rank, key in enumerate(ranking):
                            scores[key] += self.settings.bridge_weight * weight / (60 + rank + 1)
            if not scores:
                # Finite fallback preserves evidence availability for image-only/generic queries.
                scores.update((key, 0.0) for key in docs)
            ordered = sorted(scores, key=lambda key: (-scores[key], key))
            selected = []
            covered = defaultdict(set)
            for key in ordered:
                doc = docs[key]
                group = (doc["request_id"], doc["session_id"])
                positions = set(range(doc["start_seq"], doc["end_seq"]))
                # Skip a window only if all its source messages are already represented.
                # Single-message long spans must all remain eligible.
                if len(positions) > 1 and positions <= covered[group]:
                    continue
                content = json.loads(doc["content"])
                selected.append({"id": key, "content": content, "score": scores[key]})
                covered[group].update(positions)
                if len(selected) >= req.top_k:
                    break
            media_bytes = sum(len(p["image_url"]["url"].split(",", 1)[1]) * 3 // 4
                              for row in selected if isinstance(row["content"], list)
                              for p in row["content"] if p["type"] == "image_url")
            if media_bytes > 30 * 1024 * 1024:
                raise ValueError("Search media exceeds 30 MiB; unsupported response, no silent truncation")
            return selected

