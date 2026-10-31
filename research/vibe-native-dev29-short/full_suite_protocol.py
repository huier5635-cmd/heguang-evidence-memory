"""Full-suite evidence: retain exact node IDs; summary collisions never overwrite failures.

Development protocol only. Does not change legacy evaluation receipts or claim an
upstream score. Summary identity mirrors whitespace extraction, but pass checks
require every concrete member and all original passing tests to stay passing.
"""
from collections import defaultdict
import hashlib


def exact_outcomes(collected, events):
    if len(collected) != len(set(collected)):
        raise ValueError('Duplicate collected node identity')
    outcomes = {}
    grouped = defaultdict(list)
    subtests = []
    known = set(collected)
    for event in events:
        if event['nodeid'] not in known:
            raise ValueError('Report refers to uncollected test')
        if event['when'] not in {'setup', 'call', 'teardown'} or event['outcome'] not in {'passed', 'failed', 'skipped'}:
            raise ValueError('Invalid test report phase or outcome')
        if event.get('is_subtest'):
            if (event['when'] != 'call' or not isinstance(event.get('subtest_context'), str)
                    or event.get('report_type', '').rsplit('.', 1)[-1] not in {'SubtestReport', 'SubTestReport'}):
                raise ValueError('Invalid subtest report')
            subtests.append(event)
        else:
            grouped[event['nodeid']].append(event)
    for node in collected:
        records = grouped[node]
        phases = [r['when'] for r in records]
        if len(phases) != len(set(phases)):
            raise ValueError('Repeated phase report; retries cannot replace failures')
        if any(r['outcome'] == 'failed' and r['when'] != 'call' for r in records):
            outcomes[node] = 'error'
        elif any(r['outcome'] == 'failed' for r in records):
            outcomes[node] = 'failed'
        elif any(r.get('wasxfail') and r['outcome'] == 'skipped' for r in records):
            outcomes[node] = 'xfail'
        elif any(r.get('wasxfail') and r['outcome'] == 'passed' and r['when'] == 'call' for r in records):
            outcomes[node] = 'xpass'
        elif any(r['outcome'] == 'skipped' for r in records):
            outcomes[node] = 'skipped'
        elif set(phases) == {'setup', 'call', 'teardown'}:
            outcomes[node] = 'passed'
        else:
            outcomes[node] = 'incomplete'
    occurrences = defaultdict(int)
    for event in subtests:
        parent = event['nodeid']
        context = hashlib.sha256(event['subtest_context'].encode()).hexdigest()
        occurrences[(parent, context)] += 1
        child = parent + '::_qianora_subtest:' + context + ':' + str(occurrences[(parent, context)])
        if event['outcome'] == 'failed':
            outcome = 'failed'
        elif event.get('wasxfail'):
            outcome = 'xfail' if event['outcome'] == 'skipped' else 'xpass'
        else:
            outcome = event['outcome']
        outcomes[child] = outcome
        # Keep the child evidence as well as the parent failure. A later parent
        # pass can never overwrite an earlier failed subtest.
        if outcome == 'failed' and outcomes[parent] not in {'error', 'incomplete'}:
            outcomes[parent] = 'failed'
    return outcomes


def summary_groups(nodes):
    groups = defaultdict(list)
    for node in nodes:
        if not isinstance(node, str) or not node.strip() or '\x00' in node:
            raise ValueError('Invalid concrete test node')
        parent = node.split('::_qianora_subtest:', 1)[0]
        groups[parent.split()[0]].append(node)
    return dict(groups)


def assess(baseline, candidate, fail_to_pass, pass_to_pass, *, baseline_exit, candidate_exit,
           baseline_collection_errors=0, candidate_collection_errors=0, guard_reason=None):
    if (not fail_to_pass or len(set(fail_to_pass)) != len(fail_to_pass)
            or len(set(pass_to_pass)) != len(pass_to_pass) or set(fail_to_pass) & set(pass_to_pass)):
        raise ValueError('Invalid declared outcomes')
    groups = summary_groups(baseline)
    missing = sorted(set(fail_to_pass + pass_to_pass) - groups.keys())
    collisions = {k: v for k, v in groups.items() if len(v) > 1 and k in set(fail_to_pass + pass_to_pass)}
    baseline_ok = bool(not guard_reason and baseline_exit == 1 and not baseline_collection_errors and not missing)
    baseline_ok &= all(any(baseline[n] == 'failed' for n in groups.get(key, []))
                       and all(baseline[n] in {'failed', 'passed'} for n in groups.get(key, [])) for key in fail_to_pass)
    passlike = {'passed', 'xpass'}
    maintained = passlike | {'xfail'}
    baseline_ok &= all(any(baseline[n] in maintained for n in groups.get(key, []))
                       and all(baseline[n] in maintained | {'skipped'} for n in groups.get(key, []))
                       for key in pass_to_pass)
    # Baseline failures outside declared failing summary groups make this an
    # environment/protocol problem; do not silently ignore them.
    declared_failures = {n for key in fail_to_pass for n in groups.get(key, [])}
    baseline_ok &= all(value in maintained | {'skipped'} or (n in declared_failures and value == 'failed')
                       for n, value in baseline.items())
    changed_collection = set(baseline) != set(candidate)
    regressions = sorted(n for n, value in baseline.items() if value in passlike and candidate.get(n) not in passlike
                         or value == 'xfail' and candidate.get(n) not in maintained)
    resolved = bool(baseline_ok and not changed_collection and not regressions
                    and candidate_exit == 0 and not candidate_collection_errors and not guard_reason
                    and all(candidate.get(n) in passlike for n in declared_failures)
                    and all(value in passlike or (value in {'skipped', 'xfail'}
                                                 and baseline.get(n) == value)
                            for n, value in candidate.items()))
    return {'baseline_admissible': bool(baseline_ok), 'resolved': resolved,
            'missing_summary_identities': missing, 'colliding_summary_identities': collisions,
            'changed_collection': changed_collection, 'regressions': regressions,
            'official_score': False, 'full_allowed': False}
