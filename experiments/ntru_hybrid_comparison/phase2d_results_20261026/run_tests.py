"""Bounded formal unittest run; export actual process-supervision observations."""

from datetime import datetime, timezone
import json
from pathlib import Path
import time
import unittest


HERE = Path(__file__).resolve().parent


def main():
    started = time.monotonic()
    suite = unittest.defaultTestLoader.discover("experiments/ntru_hybrid_comparison/tests", top_level_dir=".")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    from experiments.ntru_hybrid_comparison.tests import test_supervisor
    observations = test_supervisor.OBSERVATIONS
    summary = {"schema": "ntru-phase2d-test-summary-v1", "completed_utc": datetime.now(timezone.utc).isoformat(),
               "tests_run": result.testsRun, "passed": result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped),
               "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped),
               "unittest_wall_seconds": time.monotonic() - started, "successful": result.wasSuccessful(),
               "real_supervisor_observation_count": len(observations),
               "real_lattice_calls": 0,
               "scope": "pure algorithm/state tests plus actual lightweight process/memory fixtures; no lattice backend computation"}
    (HERE / "tests_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (HERE / "process_observations.json").write_text(json.dumps(observations, indent=2) + "\n")
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    main()
