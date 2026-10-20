import re
import unicodedata


STOP = set("a an the is are was were be been being do does did have has had how what which when where why who whom whose i me my we our you your he his she her it its they their to from in on at for of with and or but as by about that this these those can could would should will may might not only all any some than then if so very also please tell answer".split())


def terms(text: str, *, filter_stop: bool = False) -> list[str]:
    text = unicodedata.normalize("NFKC", text)
    # Keep code identifiers, their camel/snake components, Unicode words, CJK bigrams.
    out = []
    for word in re.findall(r"[^\W_]+(?:_[^\W_]+)*", text, re.UNICODE):
        if re.search(r"[\u3400-\u9fff]", word):
            out.extend(word[i:i + 2].lower() for i in range(max(1, len(word) - 1)))
        else:
            parts = re.sub(r"([a-z])([A-Z])", r"\1 \2", word).replace("_", " ").split()
            out.extend([word.lower()] + ([p.lower() for p in parts] if len(parts) > 1 else []))
    return [w for w in out if not filter_stop or w not in STOP]


def split_spans(text: str, size: int, overlap: int):
    """Lossless source offsets; overlap is deliberate, no rewritten facts."""
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            boundary = max(text.rfind("\n", start + size // 2, end), text.rfind(". ", start + size // 2, end))
            if boundary > start:
                end = boundary + 1
        yield start, end, text[start:end]
        if end == len(text):
            break
        start = max(start + 1, end - overlap)


def quoted_entities(text: str):
    # An unsupervised bridge cue; never generates an answer or infers a fact.
    found = re.findall(r"\b[A-Z][a-z]+(?:[ -][A-Z][a-z]+){0,2}\b", text)
    return [x for x in found if x.lower() not in STOP and len(x) > 2]

