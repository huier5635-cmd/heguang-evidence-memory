"""Bind original BASE events and import proof before prospective assessment."""
import hashlib

from feature_import_protocol import assess


def assessment(case, original, baseline, candidate, declarations):
    if (not baseline['preflight_passed'] or not candidate['preflight_passed']
            or not baseline['events'] or not candidate['events']):
        return {'baseline_admissible':False,'resolved':False,'reason':'Missing native evidence'}
    try:
        if original['base_commit']!=case['base_commit'] or original['image_id']!=case['image_id']:
            raise ValueError('Original BASE identity differs')
        for execution in (baseline,candidate):
            meta=execution['preflight']
            if (meta['base_commit']!=case['base_commit']
                    or meta['python_version']!=original['python_version']
                    or meta['python_executable']!=original['python_executable']):
                raise ValueError('Original BASE runtime differs')
        inputs=baseline['preflight']['feature_import_inputs']
        if inputs!=candidate['preflight']['feature_import_inputs'] or inputs.get('captured_before_proposal_application') is not True:
            raise ValueError('Original module proof differs between arms')
        for logical,source in inputs['arguments']['original_sources'].items():
            lineage=inputs['source_lineage'][logical]
            actual=lineage['actual_path']
            digest=hashlib.sha256(source.encode('utf-8')).hexdigest()
            if digest!=lineage['sha256'] or digest!=case['expected_source_files'].get(actual):
                raise ValueError('Import witness does not match frozen BASE source')
        return assess(original['record'],baseline['events'],candidate['events'],
            declarations['fail_to_pass'],declarations['pass_to_pass'],
            **inputs['arguments'],guard_reason=baseline['guard_reason'] or candidate['guard_reason'])
    except (ValueError,KeyError,TypeError) as error:
        return {'baseline_admissible':False,'resolved':False,
                'reason':'Prospective evidence protocol rejected reports: '+str(error),
                'official_score':False,'full_allowed':False}
