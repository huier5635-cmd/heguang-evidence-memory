import hashlib
import re
import unicodedata

STOP = set("a an the is are was were be been being do does did have has had how what which when where why who i me my we our you your he his she her it its they their to from in on at for of with and or but as by about that this these those can could would should will may might only all any some than then if so very also please tell answer fix issue code repository task implement change bug error test".split())
KEYWORDS = set("def class return import from as if else elif for while try except finally with lambda yield true false none null var const let function public private static void int string new this self end do fn pub impl use mod package func type struct interface nil await async export extends throw catch".split())
WORD = re.compile(r"[^\W_]+(?:_[^\W_]+)*", re.UNICODE)
PATH = re.compile(r"(?<![\w])(?:[\w@.\-]+[/\\])*[\w@.\-]+\.(?:py|pyi|js|jsx|ts|tsx|go|rs|java|php|c|h|cc|cpp|hpp|json|ya?ml|toml|md|rb|swift|kt|sql|sh|bash)(?![\w])", re.I)
IDENTIFIER = re.compile(r"\b[A-Za-z_][A-Za-z_0-9]*(?:(?:::|\.)[A-Za-z_][A-Za-z_0-9]*)*\b")


def words(text, split=True, stop=True):
    out = []
    for word in WORD.findall(unicodedata.normalize("NFKC", text)):
        if re.search(r"[\u3400-\u9fff]", word):
            out.extend(word[i:i + 2].lower() for i in range(max(1, len(word) - 1)))
            continue
        lowered = word.lower()
        if not stop or lowered not in STOP:
            out.append(lowered)
        if split:
            components = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", word)
            components = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", components).replace("_", " ").split()
            if len(components) > 1:
                out.extend(part.lower() for part in components if not stop or part.lower() not in STOP)
    return out


def technical(text):
    paths = set()
    for match in PATH.finditer(text):
        path = match.group().replace("\\", "/").removeprefix("./").lower()
        for marker in ("/blob/", "/raw/"):
            if marker in path:
                suffix = path.split(marker, 1)[1]
                if "/" in suffix:
                    path = suffix.split("/", 1)[1]
                break
        paths.update([path, path.rsplit("/", 1)[-1]])
        if path.startswith(("a/", "b/")):
            paths.add(path[2:])
    symbols = set()
    urls = [(match.start(), match.end()) for match in re.finditer(r"https?://[^\s<>]+", text)]
    for match in IDENTIFIER.finditer(text):
        if any(start <= match.start() < end for start, end in urls):
            continue
        name = match.group()
        tail = text[match.end():match.end() + 3].lstrip()
        if len(name) > 2 and name.lower() not in KEYWORDS and (
            "_" in name or "::" in name or "." in name or re.search(r"[a-z][A-Z]", name)
            or tail.startswith("(") or text[max(0, match.start() - 1):match.start()] == "`"
        ):
            symbols.add(name.lower())
    return paths, symbols


def encoded(values, prefix):
    return [prefix + hashlib.sha256(value.encode()).hexdigest()[:24] for value in sorted(values)]


def chunks(text, size, overlap):
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            boundary = text.rfind("\n", start + size // 2, end)
            if boundary >= 0:
                end = boundary + 1
        yield start, end, text[start:end]
        if end == len(text):
            break
        start = max(start + 1, end - overlap)
