"""Validate the predeclared native environment without shell evaluation."""


def checked_environment(case, contract):
    expected=contract['commands'][case['instance_id']].get('environment',{})
    actual=case.get('test_environment',{})
    if not isinstance(actual,dict) or actual!=expected:
        raise ValueError('Native test environment differs from frozen contract')
    if actual:
        raise ValueError('Unsupported native environment override')
    return actual
