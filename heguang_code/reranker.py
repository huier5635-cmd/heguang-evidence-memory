"""Bounded local query/passage scoring; optional, offline and free of query caches."""
import hashlib
import json
import math
import threading

MODEL_FILE = "onnx/model_quint8_avx2.onnx"
MODEL_SHA256 = "c80a8b34256ea453093d612e3ac48d3d965a0c0a48c7906709af8b8e28461bf9"
MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L6-v2@233902d"


def fit_budget(rows, *, top_k, characters):
    """Select complete original records in rank order, never cut their text."""
    result, used = [], 0
    for row in rows:
        if used + len(row["content"]) <= characters:
            result.append(row)
            used += len(row["content"])
            if len(result) >= top_k:
                break
    return result


def reorder(rows, pair_scores, weight=1):
    if len(pair_scores) > len(rows) or any(not math.isfinite(value) for value in pair_scores):
        raise RuntimeError("Invalid reranker output")
    if not pair_scores:
        return rows
    count = len(pair_scores)
    semantic = sorted(range(count), key=lambda index: (-pair_scores[index], rows[index]["id"]))
    ranks = {index: rank for rank, index in enumerate(semantic, 1)}
    scores = {index: 1 / (60 + index + 1) + weight / (60 + ranks[index]) for index in range(count)}
    order = sorted(range(count), key=lambda index: (-scores[index], rows[index]["id"]))
    result = [{**rows[index], "score": scores[index]} for index in order]
    # Unscored tail keeps its original order and remains below the scored prefix.
    floor = result[-1]["score"]
    result.extend({**row, "score": floor / (1 + (index + 1) / 60)}
                  for index, row in enumerate(rows[count:]))
    return result


class PairScorer:
    def __init__(self, model_dir, *, threads=2):
        if type(threads) is not int or threads not in (1, 2):
            raise ValueError("Reranker threads must be 1 or 2")
        # Imports are optional: the lexical service has no model dependencies.
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer
        self.np = np
        receipt = json.loads((model_dir / "receipt.json").read_text(encoding="utf-8"))
        for filename in (MODEL_FILE, "tokenizer.json"):
            expected = next(row["sha256"] for row in receipt["files"] if row["path"] == filename)
            with (model_dir / filename).open("rb") as source:
                actual = hashlib.file_digest(source, "sha256").hexdigest()
            if actual != expected or (filename == MODEL_FILE and actual != MODEL_SHA256):
                raise RuntimeError("Pinned reranker checksum mismatch")
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
        options.enable_cpu_mem_arena = False
        if threads == 1:
            options.add_session_config_entry("session.intra_op.allow_spinning", "0")
            options.add_session_config_entry("session.inter_op.allow_spinning", "0")
        self.session = ort.InferenceSession(str(model_dir / MODEL_FILE), sess_options=options,
                                           providers=["CPUExecutionProvider"])
        self.tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self.tokenizer.no_truncation()
        self.tokenizer.no_padding()
        self.cls, self.sep, self.pad = [self.tokenizer.token_to_id(value) for value in ("[CLS]", "[SEP]", "[PAD]")]
        if any(value is None for value in (self.cls, self.sep, self.pad)):
            raise RuntimeError("Invalid reranker special tokens")
        self.inputs = {item.name for item in self.session.get_inputs()}
        self.lock = threading.Lock()

    def scores(self, query, documents):
        if len(documents) > 16:
            raise RuntimeError("Reranker candidate limit exceeded")
        # A single shared CPU model cannot create unbounded concurrent tensors.
        with self.lock:
            return self._scores(query, documents)

    def _scores(self, query, documents):
        np = self.np
        query_tokens = self.tokenizer.encode(query[:20000], add_special_tokens=False).ids
        if len(query_tokens) > 192:
            query_tokens = query_tokens[:128] + query_tokens[-64:]
        output = []
        for text in documents:
            tokens = self.tokenizer.encode(text[:12000], add_special_tokens=False).ids
            width = 512 - len(query_tokens) - 3
            starts = sorted(set([0, max(0, (len(tokens) - width) // 2), max(0, len(tokens) - width)]))
            pairs = []
            for start in starts:
                ids = [self.cls, *query_tokens, self.sep, *tokens[start:start + width], self.sep]
                segments = [0] * (len(query_tokens) + 2) + [1] * (len(ids) - len(query_tokens) - 2)
                pairs.append((ids, segments))
            length = max(len(ids) for ids, _ in pairs)
            feeds = {"input_ids": np.asarray([ids + [self.pad] * (length - len(ids)) for ids, _ in pairs], dtype=np.int64),
                     "attention_mask": np.asarray([[1] * len(ids) + [0] * (length - len(ids)) for ids, _ in pairs], dtype=np.int64),
                     "token_type_ids": np.asarray([segments + [0] * (length - len(segments)) for _, segments in pairs], dtype=np.int64)}
            logits = self.session.run(None, {name: value for name, value in feeds.items() if name in self.inputs})[0].reshape(-1)
            if len(logits) != len(pairs) or not np.all(np.isfinite(logits)):
                raise RuntimeError("Invalid reranker model output")
            output.append(float(logits.max()))
        return output
