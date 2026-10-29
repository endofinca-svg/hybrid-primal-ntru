"""Bounded existing regression suite; result files go only to this stage."""

from datetime import datetime, timezone
import json
from pathlib import Path
import time
import unittest


HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "preflight_existing"


def main():
    started = time.monotonic()
    suite = unittest.defaultTestLoader.discover(
        "experiments/ntru_hybrid_comparison/tests", top_level_dir=".")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    from experiments.ntru_hybrid_comparison.tests import test_supervisor
    summary = {
        "schema": "ntru-stage3-existing-tests-v1",
        "completed_utc": datetime.now(timezone.utc).isoformat(),
        "tests_run": result.testsRun,
        "passed": result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped),
        "failures": len(result.failures), "errors": len(result.errors),
        "skipped": len(result.skipped), "successful": result.wasSuccessful(),
        "unittest_wall_seconds": time.monotonic() - started,
        "real_supervisor_observation_count": len(test_supervisor.OBSERVATIONS),
        "real_lattice_calls": 0,
        "scope": "Existing comparison regression suite: pure correctness/mocks plus bounded real process and memory supervision fixtures; no n128 numerical jobs.",
    }
    for name, document in (("tests_summary.json", summary),
                           ("process_observations.json", test_supervisor.OBSERVATIONS)):
        with (OUTPUT / name).open("x") as stream:
            json.dump(document, stream, indent=2)
            stream.write("\n")
    print(json.dumps(summary))
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
