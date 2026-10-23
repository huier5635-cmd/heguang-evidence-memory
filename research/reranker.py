"""Pinned local cross-encoder pair scoring; no independent embeddings or generation."""
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
import numpy as np
import onnxruntime as ort
import psutil
from tokenizers import Tokenizer

MODEL_FILE = "onnx/model_quint8_avx2.onnx"
MODEL_SHA256 = "c80a8b34256ea453093d612e3ac48d3d965a0c0a48c7906709af8b8e28461bf9"


class Reranker:
    def __init__(self, model_dir: Path, cache: Path, seconds=240, rss_mib=512):
        self.started = time.perf_counter()
        self.seconds, self.rss_limit = seconds, rss_mib * 1024 ** 2
        self.process = psutil.Process()
        self.observed_rss, self.batches, self.new_pairs, self.truncated_queries, self.windowed_documents = 0, 0, 0, 0, 0
        receipt = json.loads((model_dir / "receipt.json").read_text(encoding="utf-8"))
        for filename in (MODEL_FILE, "tokenizer.json"):
            expected = next(row["sha256"] for row in receipt["files"] if row["path"] == filename)
            with (model_dir / filename).open("rb") as source:
                actual = hashlib.file_digest(source, "sha256").hexdigest()
            if actual != expected or (filename == MODEL_FILE and actual != MODEL_SHA256):
                raise RuntimeError("Pinned reranker checksum mismatch")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
        options.enable_cpu_mem_arena = False
        self.session = ort.InferenceSession(str(model_dir / MODEL_FILE), sess_options=options,
                                           providers=["CPUExecutionProvider"])
        self.tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self.tokenizer.no_truncation()
        self.tokenizer.no_padding()
        self.cls, self.sep, self.pad = [self.tokenizer.token_to_id(value) for value in ("[CLS]", "[SEP]", "[PAD]")]
        self.inputs = {item.name for item in self.session.get_inputs()}
        self.signature = json.dumps({"model": MODEL_SHA256,
            "tokenizer": next(row["sha256"] for row in receipt["files"] if row["path"] == "tokenizer.json"),
            "query_token_limit": 192, "query_selection": "head128_tail64", "max_pair_tokens": 512,
            "document_windows": "up to 3 evenly spaced; max logit", "query_char_cap": 20000,
            "document_char_cap": 12000}, sort_keys=True)
        cache.parent.mkdir(parents=True, exist_ok=True)
        self.cache = sqlite3.connect(cache)
        self.cache.execute("CREATE TABLE IF NOT EXISTS pair_scores(key TEXT PRIMARY KEY,score REAL NOT NULL)")
        self.guard()

    def guard(self):
        rss = self.process.memory_info().rss
        self.observed_rss = max(self.observed_rss, rss)
        if rss > self.rss_limit or time.perf_counter() - self.started > self.seconds:
            raise RuntimeError("Local reranker research resource guard")

    def scores(self, query, documents):
        self.guard()
        query_tokens = self.tokenizer.encode(query[:20000], add_special_tokens=False).ids
        if len(query_tokens) > 192:
            self.truncated_queries += 1
            query_tokens = query_tokens[:128] + query_tokens[-64:]
        output, pending = [], []
        for text in documents:
            key = hashlib.sha256((self.signature + "\0" + query + "\0" + text).encode()).hexdigest()
            row = self.cache.execute("SELECT score FROM pair_scores WHERE key=?", (key,)).fetchone()
            if row:
                if not np.isfinite(row[0]):
                    raise RuntimeError("Invalid cached pair score")
                output.append(row[0])
                continue
            tokens = self.tokenizer.encode(text[:12000], add_special_tokens=False).ids
            width = 512 - len(query_tokens) - 3
            starts = sorted(set([0, max(0, (len(tokens) - width) // 2), max(0, len(tokens) - width)]))
            self.windowed_documents += len(starts) > 1
            pairs = []
            for start in starts:
                ids = [self.cls, *query_tokens, self.sep, *tokens[start:start + width], self.sep]
                segments = [0] * (len(query_tokens) + 2) + [1] * (len(ids) - len(query_tokens) - 2)
                pairs.append((ids, segments))
            self.guard()
            length = max(len(ids) for ids, _ in pairs)
            feeds = {"input_ids": np.asarray([ids + [self.pad] * (length - len(ids)) for ids, _ in pairs], dtype=np.int64),
                     "attention_mask": np.asarray([[1] * len(ids) + [0] * (length - len(ids)) for ids, _ in pairs], dtype=np.int64),
                     "token_type_ids": np.asarray([segments + [0] * (length - len(segments)) for _, segments in pairs], dtype=np.int64)}
            logits = self.session.run(None, {name: value for name, value in feeds.items() if name in self.inputs})[0].reshape(-1)
            if len(logits) != len(pairs) or not np.all(np.isfinite(logits)):
                raise RuntimeError("Invalid reranker model output")
            score = float(logits.max())
            self.cache.execute("INSERT INTO pair_scores VALUES(?,?)", (key, score))
            self.cache.commit()
            self.batches += 1
            self.new_pairs += len(pairs)
            output.append(score)
            self.guard()
        return output

    def close(self):
        self.cache.close()
