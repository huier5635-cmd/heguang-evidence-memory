"""Bounded exact-source interval coalescing after scoring, before final packing."""
import hashlib
import json
import math

MAX_SPAN_CHARACTERS = 32768


def source_record(row):
    try:
        header, body = row["content"].split("\n", 1)
        if not header.startswith("[source ") or not header.endswith("]"):
            raise ValueError
        meta = json.loads(header[8:-1])
        start, end = meta["characters"]
        if (type(start) is not int or type(end) is not int or start < 0 or end < start
                or end - start != len(body) or end - start > MAX_SPAN_CHARACTERS):
            raise ValueError
        if any(not isinstance(meta[name], str) or not meta[name] for name in ("source_id", "session_id", "role")):
            raise ValueError
        if isinstance(row["score"], bool) or not math.isfinite(row["score"]):
            raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError):
        raise RuntimeError("Invalid source record for coalescing") from None
    return meta, body


def coalesce(rows):
    """Preserve candidate source union and best contributing rank/score.

    Never fetch missing source text, join gaps, cross source identities or
    mutate input. The final API budget is applied separately to intact spans.
    """
    if len(rows) > 100:
        raise RuntimeError("Source selection candidate limit exceeded")
    grouped, characters = {}, 0
    for rank, row in enumerate(rows):
        meta, body = source_record(row)
        characters += len(row["content"])
        if characters > 512000:
            raise RuntimeError("Source selection candidate limit exceeded")
        start, end = meta["characters"]
        grouped.setdefault(meta["source_id"], []).append((start, end, rank, meta, body))
    output = []

    def emit(active):
        start, end, rank, meta, text = active
        metadata = {**meta, "characters": [start, end]}
        identity = hashlib.sha256(json.dumps([meta["source_id"], start, end], separators=(",", ":")).encode()).hexdigest()[:36]
        output.append((rank, {"id": "span_" + identity,
            "content": "[source " + json.dumps(metadata, ensure_ascii=False, separators=(",", ":")) + "]\n" + text,
            "score": rows[rank]["score"]}))

    for spans in grouped.values():
        active = None
        identity = {k: v for k, v in spans[0][3].items() if k != "characters"}
        for start, end, rank, meta, body in sorted(spans, key=lambda item: (item[0], item[1], item[2])):
            if {k: v for k, v in meta.items() if k != "characters"} != identity:
                raise RuntimeError("Inconsistent source identity")
            if active is not None:
                left, right, best, old_meta, text = active
                if start <= right:
                    overlap_end = min(right, end)
                    if text[start - left:overlap_end - left] != body[:overlap_end - start]:
                        raise RuntimeError("Overlapping source text differs")
                if start <= right and max(right, end) - left <= MAX_SPAN_CHARACTERS:
                    active = (left, max(right, end), min(best, rank), old_meta, text + body[max(0, right - start):])
                    continue
                emit(active)
            active = (start, end, rank, meta, body)
        if active is not None:
            emit(active)
    return [row for _, row in sorted(output, key=lambda item: (item[0], item[1]["id"]))]
