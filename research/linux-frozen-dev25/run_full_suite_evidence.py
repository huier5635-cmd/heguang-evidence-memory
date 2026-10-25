"""Run the dataset's full pytest command and retain concrete per-phase evidence.

Use inside an isolated prepared baseline/proposal repository. This runner does
not install dependencies, select declared test IDs, apply patches or score runs.
"""
import argparse
import json
from pathlib import Path
import sys
import pytest

class Recorder:
    def __init__(self, output):
        self.output = output
        self.events = []
        self.collection_errors = 0

    def pytest_runtest_logreport(self, report):
        self.events.append({'nodeid': report.nodeid, 'when': report.when,
                            'outcome': report.outcome, 'wasxfail': hasattr(report, 'wasxfail')})

    def pytest_collectreport(self, report):
        if report.failed:
            self.collection_errors += 1

    def pytest_sessionfinish(self, session, exitstatus):
        report = {'collected': [item.nodeid for item in session.items], 'events': self.events,
                  'collection_errors': self.collection_errors, 'exit_code': int(exitstatus),
                  'test_selection_filters_added': False, 'command': ['pytest', '-rA'],
                  'junit_added_for_evidence': True, 'official_score': False, 'full_allowed': False}
        with (self.output / 'full-suite-events.json').open('x', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write('\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    # Output is separate from the checked-out repository. Original complete
    # suite is collected according to its native configuration.
    return pytest.main(['-rA', '--junitxml=' + str((args.output / 'full-suite.xml').resolve()),
                        '-p', 'no:cacheprovider'], plugins=[Recorder(args.output)])

if __name__ == '__main__':
    raise SystemExit(main())
