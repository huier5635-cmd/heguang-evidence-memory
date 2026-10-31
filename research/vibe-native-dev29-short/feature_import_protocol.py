"""Prospective prototype for a missing new local API at pytest collection.

Requires complete original-BASE event evidence and an added import proved from
fixed test/source text. Never changes old studies or infers a gold solution.
Normal collected baselines retain the unchanged strict assessment.
"""
import ast
import hashlib
from pathlib import PurePosixPath
import re

from full_suite_protocol import assess as strict_assess, exact_outcomes, summary_groups

IMPORT_ERROR = re.compile(
    r'''^E\s+ImportError:\s+cannot import name ['"]([A-Za-z_]\w*)['"] from ['"]([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)['"](?:\s|$)''', re.M)


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def safe_path(name):
    if (not isinstance(name, str) or not name.endswith('.py') or '\\' in name
            or ':' in name or PurePosixPath(name).is_absolute()
            or any(p in {'', '.', '..', '.git', '.env'} for p in name.split('/'))):
        raise ValueError('Unsafe Python source path')
    return name


def imports(text, path):
    package = safe_path(path).split('/')[:-1]
    result = set()
    for node in ast.walk(ast.parse(text, filename=path)):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level:
            if node.level > len(package):
                continue
            parts = package[:len(package) - node.level + 1]
        else:
            parts = []
        module = '.'.join(parts + (node.module.split('.') if node.module else []))
        if module and all(p.isidentifier() for p in module.split('.')):
            result.update((module, alias.name) for alias in node.names if alias.name != '*')
    return result


def module_bindings(text):
    names, uncertain = set(), [False]

    def visit(node):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            if node.name == '__getattr__':
                uncertain[0] = True
            return
        if isinstance(node, ast.Lambda):
            return
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        if isinstance(node, ast.Import):
            names.update(a.asname or a.name.split('.')[0] for a in node.names)
        if isinstance(node, ast.ImportFrom):
            if any(a.name == '*' for a in node.names):
                uncertain[0] = True
            names.update(a.asname or a.name for a in node.names)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {'exec', 'globals'}:
            uncertain[0] = True
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(ast.parse(text))
    return None if uncertain[0] else names


def import_witnesses(original_sources, original_tests, patched_tests, production_packages):
    if not production_packages or any(not p.isidentifier() for p in production_packages):
        raise ValueError('Explicit local package roots required')
    result = []
    for test_path, after in sorted(patched_tests.items()):
        safe_path(test_path)
        before = original_tests.get(test_path, '')
        for module, symbol in sorted(imports(after, test_path) - imports(before, test_path)):
            if module.split('.')[0] not in production_packages:
                continue
            base = module.replace('.', '/')
            matches = [p for p in (base + '.py', base + '/__init__.py') if p in original_sources]
            if len(matches) != 1:
                continue
            source_path = matches[0]
            bindings = module_bindings(original_sources[source_path])
            if bindings is None or symbol in bindings:
                continue
            result.append({'test_path': test_path, 'module': module, 'symbol': symbol,
                'source_path': source_path, 'source_sha256': digest(original_sources[source_path]),
                'original_test_sha256': digest(before), 'patched_test_sha256': digest(after)})
    return result


def assess(original, baseline, candidate, fail_to_pass, pass_to_pass, *,
           original_sources, original_tests, patched_tests, production_packages, guard_reason=None):
    if (not fail_to_pass or len(set(fail_to_pass)) != len(fail_to_pass)
            or len(set(pass_to_pass)) != len(pass_to_pass) or set(fail_to_pass) & set(pass_to_pass)):
        raise ValueError('Invalid declarations')
    declarations = set(fail_to_pass + pass_to_pass)
    summary_groups(declarations)
    records = (original, baseline, candidate)
    if any(r['native_command'] != original['native_command'] or r.get('test_selection_changed') is not False for r in records):
        raise ValueError('Native test selection differs')
    prior = exact_outcomes(original['collected'], original['events'])
    current = exact_outcomes(candidate['collected'], candidate['events'])
    passlike, maintained = {'passed', 'xpass'}, {'passed', 'xpass', 'xfail'}
    original_ok = bool(original['exit_code'] == 0 and not original['collection_errors']
        and prior and any(v in passlike for v in prior.values())
        and all(v in maintained | {'skipped'} for v in prior.values()))
    if not baseline['collection_errors']:
        old = strict_assess(exact_outcomes(baseline['collected'], baseline['events']), current,
            fail_to_pass, pass_to_pass, baseline_exit=baseline['exit_code'], candidate_exit=candidate['exit_code'],
            baseline_collection_errors=0, candidate_collection_errors=candidate['collection_errors'],
            guard_reason=guard_reason or (None if original_ok else 'Original BASE evidence inadmissible'))
        return dict(old, protocol='unchanged-strict', original_base_verified=original_ok)
    witnesses = import_witnesses(original_sources, original_tests, patched_tests, production_packages)
    allowed = {(w['test_path'], w['symbol'], w['module']) for w in witnesses}
    diagnostics = baseline['collection_diagnostics']
    failures = []
    for diagnostic in diagnostics:
        matches = IMPORT_ERROR.findall(diagnostic['diagnostic'])
        if (diagnostic.get('truncated') is not False or len(matches) != 1
                or (diagnostic['nodeid'], matches[0][0], matches[0][1]) not in allowed):
            failures.append('Collection failure lacks a proved newly added local API import')
    modules = {d['nodeid'] for d in diagnostics}
    if baseline['exit_code'] != 2 or not diagnostics or baseline['collection_errors'] != len(diagnostics):
        failures.append('Incomplete import-failure baseline')
    if any(key.split('::', 1)[0] not in modules for key in fail_to_pass):
        failures.append('Declared new failing test outside affected modules')
    original_groups = summary_groups(prior)
    if any(not original_groups.get(key)
           or not any(prior[n] in maintained for n in original_groups.get(key, []))
           or not all(prior[n] in maintained | {'skipped'} for n in original_groups.get(key, []))
           for key in pass_to_pass):
        failures.append('Declared preserved test lacks passing original BASE evidence')
    base_ok = bool(original_ok and not guard_reason and not failures)
    groups = summary_groups(current)
    missing = sorted(declarations - groups.keys())
    missing_original = sorted(set(prior) - current.keys())
    node_group = {n: key for key, members in groups.items() for n in members}
    unexplained = sorted(n for n in current if n not in prior and node_group[n] not in declarations)
    regressions = sorted(n for n, value in prior.items()
        if (value in passlike and current.get(n) not in passlike)
        or (value == 'xfail' and current.get(n) not in maintained)
        or (value == 'skipped' and current.get(n) != 'skipped'))
    all_required_pass = all(all(current[n] in passlike for n in groups.get(key, []))
        and groups.get(key) for key in fail_to_pass)
    no_new_nonpasses = all(v in passlike or (v in {'xfail', 'skipped'} and prior.get(n) == v)
        for n, v in current.items())
    resolved = bool(base_ok and not missing and not missing_original and not unexplained
        and not regressions and candidate['exit_code'] == 0 and not candidate['collection_errors']
        and all_required_pass and no_new_nonpasses)
    return {'protocol': 'new-local-api-import-prototype', 'original_base_verified': original_ok,
        'baseline_admissible': base_ok, 'baseline_failure_reasons': failures,
        'baseline_import_witnesses': witnesses, 'baseline_collection_equivalence_proven': False,
        'resolved': resolved, 'missing_summary_identities': missing, 'missing_original_identities': missing_original,
        'unexplained_added_identities': unexplained, 'regressions': regressions,
        'official_score': False, 'full_allowed': False}
