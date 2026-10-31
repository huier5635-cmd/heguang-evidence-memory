"""Bounded static call-shape priority for historical Python evidence.

No code is executed and no source text is changed. This is a retrieval feature,
not proof that a historical implementation or its behavior is current.
"""
import ast
import inspect
import io
import re
import textwrap
import tokenize

from .selection import source_record

POLICY = 'explicit-python-call-shape-v1'
MAX_QUERY = 20000
PATH = re.compile(r'(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.py(?![A-Za-z0-9_])')
DEF = re.compile(r'^(\s*)(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(')
CLASS = re.compile(r'^(\s*)class\s+([A-Za-z_]\w*)\b')
HUNK = re.compile(r'^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@(.*)$')


def python_blocks(query):
    if len(query) > MAX_QUERY:
        return []
    blocks, lines, fence, language = [], [], None, ''
    for line in query.splitlines():
        marker = re.fullmatch(r' {0,3}(`{3,}|~{3,})([^\r\n]*)', line)
        if marker:
            token, suffix = marker.groups()
            if fence is None:
                fence, language, lines = token, suffix.strip().casefold(), []
            elif token[0] == fence[0] and len(token) >= len(fence) and not suffix.strip():
                if language in {'', 'py', 'python', 'python3'}:
                    blocks.append('\n'.join(lines))
                fence, lines = None, []
        elif fence is not None:
            lines.append(line)
    if fence is not None or len(blocks) > 8:
        return []
    return blocks or [query]


def query_calls(query):
    calls = []
    for block in python_blocks(query):
        try:
            tree = ast.parse(block)
        except (SyntaxError, ValueError, RecursionError):
            continue
        aliases = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                aliases.update((a.asname or a.name, a.name) for a in node.names if a.name != '*')
        assigned = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                value = node.value
                factory = (value.func.id if isinstance(value, ast.Call) and isinstance(value.func, ast.Name)
                           else value.func.attr if isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute) else '')
                factory = aliases.get(factory, factory)
                owner = factory if factory and factory[0].isupper() else None
                for target in targets:
                    if isinstance(target, ast.Name):
                        assigned.setdefault(target.id, set()).add(owner)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            owner, receiver = None, 'function'
            if isinstance(node.func, ast.Name):
                name = aliases.get(node.func.id, node.func.id)
            elif isinstance(node.func, ast.Attribute):
                name, receiver = node.func.attr, 'unknown'
                obj = node.func.value
                if isinstance(obj, ast.Name):
                    options = assigned.get(obj.id, set())
                    if len(options) == 1 and None not in options:
                        owner, receiver = next(iter(options)), 'instance'
                    elif obj.id not in assigned and aliases.get(obj.id, obj.id)[:1].isupper():
                        owner, receiver = aliases.get(obj.id, obj.id), 'class'
                elif isinstance(obj, ast.Call) and isinstance(obj.func, ast.Name):
                    value = aliases.get(obj.func.id, obj.func.id)
                    if value[:1].isupper():
                        owner, receiver = value, 'instance'
            else:
                continue
            keywords = tuple(k.arg for k in node.keywords)
            complete = (not any(isinstance(a, ast.Starred) for a in node.args)
                        and None not in keywords and len(set(keywords)) == len(keywords))
            calls.append({'name': name, 'owner': owner, 'receiver': receiver,
                'positional': len(node.args), 'keywords': keywords, 'complete': complete})
            if len(calls) > 32:
                return []
    return calls


def source_lines(body):
    raw = body.splitlines()
    if any(line.startswith('diff --git ') for line in raw):
        lines, pending, remaining = [], [], None
        for line in raw:
            match = HUNK.fullmatch(line)
            if match:
                pending = []
                owner = re.search(r'\bclass\s+([A-Za-z_]\w*)', match[3])
                if owner:
                    pending.append('class ' + owner[1] + ':')
                remaining = [int(match[1]) if match[1] is not None else 1,
                             int(match[2]) if match[2] is not None else 1]
                if remaining == [0, 0]:
                    remaining = None
                continue
            if remaining is None:
                continue
            if line.startswith('\\ No newline at end of file'):
                continue
            if line.startswith('+'):
                remaining[1] -= 1; pending.append(line[1:])
            elif line.startswith('-'):
                remaining[0] -= 1
            elif line.startswith(' '):
                remaining[0] -= 1; remaining[1] -= 1; pending.append(line[1:])
            else:
                pending, remaining = [], None
                continue
            if min(remaining) < 0:
                return []
            if remaining == [0, 0]:
                lines.extend(pending); pending, remaining = [], None
        return lines
    return [re.sub(r'^\s*\d+(?:\s*\|\s?|\t|\s*:\s?|\s+(?=(?:async\s+)?def\s|class\s))', '', line)
            for line in raw]


def signature_node(lines, index):
    fragment = '\n'.join(lines[index:index + 16])[:4096]
    offsets, count = [0], 0
    for line in fragment.splitlines(keepends=True):
        count += len(line); offsets.append(count)
    depth = 0
    try:
        for token in tokenize.generate_tokens(io.StringIO(fragment).readline):
            if token.type == tokenize.OP and token.string == '(':
                depth += 1
            elif token.type == tokenize.OP and token.string == ')':
                depth -= 1
                if depth == 0:
                    end = offsets[token.end[0] - 1] + token.end[1]
                    tree = ast.parse(textwrap.dedent(fragment[:end]) + ':\n    pass\n')
                    node = tree.body[0]
                    return node if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else None
    except (SyntaxError, ValueError, RecursionError, tokenize.TokenError, IndentationError):
        return None


def signatures(body):
    lines, owners, result = source_lines(body), [], []
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        while owners and owners[-1][0] >= indent:
            owners.pop()
        owner = CLASS.match(line)
        if owner:
            owners.append((indent, owner[2])); continue
        if DEF.match(line):
            node = signature_node(lines, index)
            if node is not None:
                # Decorators can replace call semantics. The extracted header
                # alone is insufficient to bind such a callable.
                previous = index - 1
                while previous >= 0 and not lines[previous].strip():
                    previous -= 1
                node._decorated = previous >= 0 and lines[previous].lstrip().startswith('@')
                result.append((node, owners[-1][1] if owners else None))
            if len(result) > 64:
                return []
    return result


def binds(call, node):
    if not call['complete'] or getattr(node, '_decorated', False):
        return 0
    positional = [*node.args.posonlyargs, *node.args.args]
    first = positional[0].arg if positional else None
    drop = first == 'self' and call['receiver'] == 'instance'
    if first == 'cls' or first == 'self' and call['receiver'] == 'unknown':
        return 0
    parameters, required = [], len(positional) - len(node.args.defaults)
    for index, arg in enumerate(positional):
        if drop and index == 0:
            continue
        kind = inspect.Parameter.POSITIONAL_ONLY if index < len(node.args.posonlyargs) else inspect.Parameter.POSITIONAL_OR_KEYWORD
        default = inspect.Parameter.empty if index < required else object()
        parameters.append(inspect.Parameter(arg.arg, kind, default=default))
    if node.args.vararg:
        parameters.append(inspect.Parameter(node.args.vararg.arg, inspect.Parameter.VAR_POSITIONAL))
    for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults):
        parameters.append(inspect.Parameter(arg.arg, inspect.Parameter.KEYWORD_ONLY,
            default=inspect.Parameter.empty if default is None else object()))
    if node.args.kwarg:
        parameters.append(inspect.Parameter(node.args.kwarg.arg, inspect.Parameter.VAR_KEYWORD))
    try:
        signature = inspect.Signature(parameters)
        signature.bind(*[None] * call['positional'], **{k: None for k in call['keywords']})
    except ValueError:
        return 0
    except TypeError:
        return -1
    return 1


def prioritize(query, rows):
    calls = query_calls(query)
    if not calls or len(rows) > 100 or sum(len(r['content']) for r in rows) > 600000:
        return rows
    paths = set()
    for match in PATH.finditer(query):
        parts = match[0].split('/')
        if len(parts) <= 16:
            paths.update('/'.join(parts[i:]) for i in range(len(parts) - 1))
        if len(paths) > 256:
            return rows
    graded = []
    for rank, row in enumerate(rows):
        _, body = source_record(row)
        anchored = any(re.search(r'(?<![A-Za-z0-9_.-])' + re.escape(path) + r'(?![A-Za-z0-9_.-])', body) for path in paths)
        statuses = []
        for node, owner in signatures(body):
            for call in calls:
                if call['name'] != node.name:
                    continue
                if owner and call['owner'] and owner != call['owner']:
                    continue
                if not anchored and not (owner and owner == call['owner']):
                    continue
                statuses.append(binds(call, node))
        grade = 1 if statuses and all(s == 1 for s in statuses) else -1 if statuses and all(s == -1 for s in statuses) else 0
        graded.append((grade, rank, row))
    ordered = sorted(graded, key=lambda item: (-item[0], item[1]))
    if all(old[1] == new[1] for old, new in zip(graded, ordered)):
        return rows
    return [{**row, 'score': 1. / (61 + rank)} for rank, (_, _, row) in enumerate(ordered)]
