"""Public LoCoMo retrieval diagnostic for AML Add/Search systems.

This is not the official AML score. It uses the upstream public LoCoMo
evidence annotations to measure whether original dialogue evidence is found.
Only Python's standard library is required.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import statistics
import time
from urllib import error, request
import uuid


WORD = re.compile(r"[^\W_]+", re.UNICODE)
EVIDENCE_ID = re.compile(r"\bD\d+:\d+\b")
SESSION = re.compile(r"^session_(\d+)$")
DEFAULT_KS = (1, 5, 10, 20, 100)
CHAR_BUDGETS = (4000, 8000, 16000)


@dataclass(frozen=True)
class Turn:
    evidence_id: str
    speaker: str
    text: str
    session: str
    date: str


@dataclass(frozen=True)
class Doc:
    content: str
    evidence_ids: frozenset[str]


def load_dataset(path: Path) -> list[dict]:
    if path.is_dir():
        conversations = [json.loads(line) for line in
                         (path / "conversations.jsonl").open(encoding="utf-8")]
        questions = defaultdict(list)
        for line in (path / "questions.jsonl").open(encoding="utf-8"):
            question = json.loads(line)
            questions[question["sample_id"]].append(question)
        converted = []
        for source in conversations:
            conversation = {}
            for session in source["sessions"]:
                key = f"session_{session['session_index']}"
                conversation[f"{key}_date_time"] = session.get("date_time", "")
                conversation[key] = [{
                    "dia_id": message["dia_id"],
                    "speaker": message["speaker"],
                    "text": message["text"] +
                            (f"\n[caption] {message['blip_caption']}"
                             if message.get("blip_caption") else ""),
                } for message in session["messages"]]
            converted.append({"sample_id": source["sample_id"],
                              "speaker_a": source["speaker_a"],
                              "speaker_b": source["speaker_b"],
                              "conversation": conversation,
                              "qa": questions[source["sample_id"]]})
        return converted
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("LoCoMo dataset must be a JSON array")
    return data


def turns_by_session(sample: dict) -> list[tuple[str, str, list[Turn]]]:
    conversation = sample["conversation"]
    names = sorted(
        (name for name in conversation if SESSION.match(name)),
        key=lambda name: int(SESSION.match(name).group(1)),
    )
    result = []
    for name in names:
        date = str(conversation.get(f"{name}_date_time", ""))
        turns = [
            Turn(str(row["dia_id"]), str(row["speaker"]), str(row["text"]), name, date)
            for row in conversation[name]
            if row.get("dia_id") and row.get("text")
        ]
        result.append((name, date, turns))
    return result


def make_docs(sample: dict, unit: str) -> list[Doc]:
    documents = []
    for _, date, turns in turns_by_session(sample):
        if unit == "session":
            groups = [turns]
        elif unit == "turn":
            groups = [[turn] for turn in turns]
        else:
            size = int(unit.removeprefix("window"))
            groups = [turns[i : i + size] for i in range(0, len(turns), size)]
        for group in groups:
            if group:
                lines = [f"[{turn.evidence_id}] {turn.speaker}: {turn.text}" for turn in group]
                documents.append(Doc(f"Date: {date}\n" + "\n".join(lines),
                                     frozenset(turn.evidence_id for turn in group)))
    return documents


def tokens(text: str, analyzer: str) -> list[str]:
    lower = text.lower()
    if analyzer == "word":
        return WORD.findall(lower)
    compact = re.sub(r"\s+", " ", lower)
    return [compact[i : i + 3] for i in range(max(0, len(compact) - 2))]


class BM25:
    def __init__(self, documents: list[Doc], analyzer: str):
        self.documents = documents
        self.analyzer = analyzer
        self.lengths = []
        self.postings = defaultdict(list)
        for index, doc in enumerate(documents):
            counts = Counter(tokens(doc.content, analyzer))
            self.lengths.append(sum(counts.values()))
            for term, count in counts.items():
                self.postings[term].append((index, count))
        self.avgdl = statistics.mean(self.lengths) if self.lengths else 1.0

    def rank(self, query: str, k: int) -> list[Doc]:
        scores = defaultdict(float)
        n = len(self.documents)
        for term in set(tokens(query, self.analyzer)):
            posting = self.postings.get(term, ())
            if not posting:
                continue
            idf = math.log1p((n - len(posting) + 0.5) / (len(posting) + 0.5))
            for index, tf in posting:
                norm = tf + 1.2 * (0.25 + 0.75 * self.lengths[index] / self.avgdl)
                scores[index] += idf * tf * 2.2 / norm
        ranked = sorted(range(n), key=lambda i: (-scores[i], i))
        return [self.documents[i] for i in ranked[:k]]


def valid_questions(sample: dict) -> list[dict]:
    ids = {turn.evidence_id for _, _, turns in turns_by_session(sample) for turn in turns}
    return [q for q in sample["qa"]
            if q.get("question") and q.get("evidence")
            and all(str(e) in ids for e in q["evidence"])]


def score_ranking(found_at_rank: dict[str, int], gold: set[str], ks: tuple[int, ...],
                  found_at_chars: dict[str, int] | None = None) -> dict:
    result = {}
    for k in ks:
        hits = {eid for eid, rank in found_at_rank.items() if rank <= k}
        result[f"any@{k}"] = int(bool(gold & hits))
        result[f"all@{k}"] = int(gold <= hits)
    result["reciprocal_rank"] = 1 / min(found_at_rank.values()) if found_at_rank else 0.0
    if found_at_chars is not None:
        for budget in CHAR_BUDGETS:
            hits = {eid for eid, size in found_at_chars.items() if size <= budget}
            result[f"all_within_{budget}_chars"] = int(gold <= hits)
    return result


def summarize(rows: list[dict]) -> dict:
    if not rows:
        return {"questions": 0}
    names = [key for key in rows[0] if key != "category"]
    return {"questions": len(rows), **{
        name: round(sum(row[name] for row in rows) / len(rows), 4)
        for name in names
    }}


def local_eval(samples: list[dict], ks: tuple[int, ...]) -> dict:
    variants = [(unit, analyzer) for unit in ("turn", "window4", "window20", "session")
                for analyzer in ("word", "char3", "hybrid")]
    output = {"benchmark": "upstream LoCoMo, public evidence retrieval proxy", "variants": {}}
    for unit, analyzer in variants:
        rows = []
        doc_sizes = []
        for sample in samples:
            docs = make_docs(sample, unit)
            doc_sizes.extend(len(doc.content) for doc in docs)
            index = BM25(docs, analyzer) if analyzer != "hybrid" else None
            word_index = BM25(docs, "word") if analyzer == "hybrid" else None
            char_index = BM25(docs, "char3") if analyzer == "hybrid" else None
            for question in valid_questions(sample):
                gold = set(map(str, question["evidence"]))
                found, found_chars = {}, {}
                cumulative_chars = 0
                if analyzer == "hybrid":
                    fused = defaultdict(float)
                    for subindex in (word_index, char_index):
                        for rank, doc in enumerate(subindex.rank(question["question"], max(ks)), start=1):
                            fused[doc] += 1 / (60 + rank)
                    ranked_docs = sorted(fused, key=lambda doc: -fused[doc])[:max(ks)]
                else:
                    ranked_docs = index.rank(question["question"], max(ks))
                for rank, doc in enumerate(ranked_docs, start=1):
                    cumulative_chars += len(doc.content)
                    for eid in gold & doc.evidence_ids:
                        found.setdefault(eid, rank)
                        found_chars.setdefault(eid, cumulative_chars)
                rows.append({"category": str(question.get("category", "unknown")),
                             **score_ranking(found, gold, ks, found_chars)})
        categories = sorted({row["category"] for row in rows})
        output["variants"][f"{unit}-{analyzer}"] = {
            "overall": summarize(rows),
            "by_category": {cat: summarize([r for r in rows if r["category"] == cat])
                            for cat in categories},
            "mean_doc_chars": round(statistics.mean(doc_sizes), 1) if doc_sizes else 0,
        }
    return output


def post_json(url: str, payload: dict, headers: dict, timeout: float) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(url, data=body,
                          headers={"Content-Type": "application/json", **headers},
                          method="POST")
    try:
        with request.urlopen(req, timeout=timeout) as response:
            if response.status != 200:
                raise ValueError(f"{url}: HTTP {response.status}; expected 200")
            return json.load(response)
    except error.HTTPError as exc:
        details = exc.read(300).decode("utf-8", errors="replace")
        raise RuntimeError(f"{url}: HTTP {exc.code}: {details}") from exc


def auth_headers(kind: str) -> dict:
    if kind == "none":
        return {}
    key = os.getenv("AML_MEMORY_SYSTEM_KEY")
    if not key:
        raise ValueError("Set AML_MEMORY_SYSTEM_KEY in the environment; do not pass a key on the command line")
    if kind == "x-api-key":
        return {"X-Api-Key": key}
    return {"Authorization": f"{kind.title()} {key}"}


def chunk_turns(turns: list[Turn]) -> list[list[Turn]]:
    chunks, current, words = [], [], 0
    for turn in turns:
        count = len(WORD.findall(turn.text))
        if current and (len(current) >= 20 or words + count > 2000):
            chunks.append(current)
            current, words = [], 0
        current.append(turn)
        words += count
    if current:
        chunks.append(current)
    return chunks


def api_eval(samples: list[dict], args: argparse.Namespace, ks: tuple[int, ...]) -> dict:
    headers = auth_headers(args.auth)
    run_id = args.run_id or uuid.uuid4().hex[:12]
    rows, failures, latencies = [], [], []
    for sample in samples[:args.samples]:
        user_id = f"public-locomo:{run_id}:{sample['sample_id']}"
        turns = [turn for _, _, group in turns_by_session(sample) for turn in group]
        turn_text = {turn.evidence_id: turn.text for turn in turns}
        for session, date, group in turns_by_session(sample):
            for i, chunk in enumerate(chunk_turns(group)):
                request_id = f"{user_id}:{session}:chunk-{i}"
                payload = {
                    "request_id": request_id,
                    "user_id": user_id,
                    "session_id": f"{user_id}:{session}",
                    "messages": [{"role": "user" if turn.speaker == sample.get("speaker_a") else "assistant",
                                  "content": f"[{turn.evidence_id}] {turn.speaker}: {turn.text} (session date: {date})"}
                                 for turn in chunk],
                }
                response = post_json(args.add_url, payload, headers, args.timeout)
                if response.get("success") is not True or any(
                    response.get(field) != payload[field]
                    for field in ("request_id", "user_id", "session_id")
                ):
                    raise ValueError(f"Invalid Add response for {request_id}")
        for question in valid_questions(sample)[:args.questions]:
            gold = set(map(str, question["evidence"]))
            payload = {"query": question["question"], "user_id": user_id,
                       "top_k": max(ks)}
            started = time.monotonic()
            response = post_json(args.search_url, payload, headers, args.timeout)
            latencies.append(time.monotonic() - started)
            data = response.get("data")
            if not isinstance(data, list) or len(data) > max(ks):
                raise ValueError("Invalid Search data array or top_k exceeded")
            found, found_chars = {}, {}
            cumulative_chars = 0
            for rank, candidate in enumerate(data, start=1):
                if not isinstance(candidate, dict) or not candidate.get("id") or not candidate.get("content"):
                    raise ValueError("Search candidate requires nonempty id and content")
                content = candidate["content"]
                if not isinstance(content, str):
                    continue  # This script only scores textual evidence.
                cumulative_chars += len(content)
                cited_ids = set(EVIDENCE_ID.findall(content))
                for eid in gold:
                    original = turn_text[eid]
                    if eid in cited_ids or (len(original) >= 20 and original in content):
                        found.setdefault(eid, rank)
                        found_chars.setdefault(eid, cumulative_chars)
            metrics = score_ranking(found, gold, ks, found_chars)
            row = {"category": str(question.get("category", "unknown")), **metrics}
            rows.append(row)
            if not metrics[f"all@{max(ks)}"]:
                failures.append({"sample_id": sample["sample_id"],
                                 "question": question["question"],
                                 "gold_evidence_ids": sorted(gold),
                                 "found_evidence_ids": sorted(found)})
    return {
        "benchmark": "upstream LoCoMo, public evidence retrieval proxy",
        "run_id": run_id,
        "overall": summarize(rows),
        "by_category": {cat: summarize([r for r in rows if r["category"] == cat])
                        for cat in sorted({r["category"] for r in rows})},
        "search_latency_seconds_p50": round(statistics.median(latencies), 3) if latencies else None,
        "failures": failures,
        "note": "ID/text matching may undercount systems that paraphrase or summarize original evidence.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("inspect", "local", "api"))
    parser.add_argument("--dataset", type=Path, required=True,
                        help="LoCoMo locomo10.json or LoCoMo-Refined data/public directory")
    parser.add_argument("--output", type=Path, help="Optional JSON report path")
    parser.add_argument("--ks", type=int, nargs="+", default=DEFAULT_KS)
    parser.add_argument("--add-url")
    parser.add_argument("--search-url")
    parser.add_argument("--auth", choices=("none", "bearer", "token", "x-api-key"), default="none")
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--questions", type=int, default=1000000)
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--run-id", help="Reuse to retry the same deterministic public test scope")
    args = parser.parse_args()
    ks = tuple(sorted(set(args.ks)))
    if not ks or ks[0] < 1 or ks[-1] > 100:
        parser.error("--ks values must be between 1 and 100")
    samples = load_dataset(args.dataset)
    if args.mode == "inspect":
        result = {"samples": len(samples),
                  "sessions": sum(len(turns_by_session(s)) for s in samples),
                  "turns": sum(sum(len(g) for _, _, g in turns_by_session(s)) for s in samples),
                  "questions": sum(len(s["qa"]) for s in samples),
                  "questions_with_valid_evidence": sum(len(valid_questions(s)) for s in samples)}
    elif args.mode == "local":
        result = local_eval(samples, ks)
    else:
        if not args.add_url or not args.search_url:
            parser.error("api mode requires --add-url and --search-url")
        result = api_eval(samples, args, ks)
    if args.mode != "inspect" and args.dataset.is_dir():
        result["benchmark"] = "LoCoMo-Refined public questions, evidence retrieval proxy"
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
