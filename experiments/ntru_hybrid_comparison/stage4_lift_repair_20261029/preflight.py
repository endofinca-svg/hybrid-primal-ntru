"""Relevant regressions for the lifting correction, with durable test results."""
import json
from pathlib import Path
import time
import xml.etree.ElementTree as ET

import pytest

HERE = Path(__file__).resolve().parent


def main():
    started = time.monotonic()
    output = HERE / 'preflight_regression'
    output.mkdir(exist_ok=True)
    paths = [
        'experiments/ntru_e2e/tests/test_adapter.py',
        'experiments/ntru_e2e/tests/test_phase2_adapter.py',
        'experiments/ntru_e2e/tests/test_phase2_instrumentation.py',
        'experiments/ntru_hybrid_comparison/tests',
    ]
    paths += [str(path) for path in sorted(
        (HERE.parents[1] / 'ntru_e2e/tests').glob('test_lift*.py'))]
    paths += [str(path) for path in sorted(HERE.glob('test_*.py'))]
    junit = output / 'tests.xml'
    arguments = ['-q', '--disable-warnings', '--junitxml=' + str(junit), *paths]
    (output / 'pytest_arguments.json').write_text(json.dumps(arguments, indent=2) + '\n')
    code = int(pytest.main(arguments))
    suites = ET.parse(junit).getroot().findall('testsuite') if junit.exists() else []
    summary = {
        'passed': code == 0,
        'exit_code': code,
        'tests': sum(int(s.get('tests', 0)) for s in suites),
        'failures': sum(int(s.get('failures', 0)) for s in suites),
        'errors': sum(int(s.get('errors', 0)) for s in suites),
        'skipped': sum(int(s.get('skipped', 0)) for s in suites),
        'wall_seconds': time.monotonic() - started,
        'arguments': arguments,
        'scope': 'adapter coordinate transport, mapping, public reconstruction, fixed backends, and existing comparison regressions',
    }
    (output / 'result.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary), flush=True)
    raise SystemExit(code)


if __name__ == '__main__':
    main()
