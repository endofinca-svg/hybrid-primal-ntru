"""External-owner budget/accounting tests using an explicit fake supervisor.

No process, lattice reduction, or decoder is started.  Candidate flags in the
fake worker are intentionally untrusted; the real public validator remains in
the owner path.  Real process/backend acceptance is a separate test artifact.
"""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiments.ntru_hybrid_comparison.progress import TargetProgress
from experiments.ntru_hybrid_comparison.reduction import ReductionProtocol, protocol_manifest
from experiments.ntru_hybrid_comparison.standalone import run_experiment, run_trial
from experiments.ntru_hybrid_comparison.summarize import summarize_trials


class Clock:
    def __init__(self, now=100.0):
        self.now = float(now)

    def __call__(self):
        return self.now


class FakeSupervisor:
    """Write realistic durable worker facts, without executing its argv."""

    def __init__(self, clock, outcomes):
        self.clock, self.outcomes = clock, list(outcomes)
        self.requests, self.calls = [], []

    def __call__(self, argv, **kwargs):
        if len(self.requests) >= len(self.outcomes):
            raise AssertionError("owner started an unexpected extra attempt")
        request_path = Path(argv[argv.index("--request") + 1])
        event_path = Path(argv[argv.index("--events") + 1])
        result_path = Path(argv[argv.index("--result") + 1])
        request = json.loads(request_path.read_text())
        self.requests.append(deepcopy(request))
        self.calls.append(deepcopy(kwargs))
        outcome = self.outcomes[len(self.requests) - 1]
        if outcome.get("raise") is not None:
            raise outcome["raise"]
        start = self.clock()
        self.clock.now += outcome.get("duration", 0.5)
        if outcome.get("late_candidate"):
            self.clock.now = max(self.clock.now, request["work_deadline"] + 0.05)
        observed = self.clock()
        progress = TargetProgress([], clock=self.clock).snapshot()
        candidates = []
        if outcome.get("candidate"):
            public = json.loads(Path(request["public_path"]).read_text())
            vector = [0] * public["n"]
            remaining = request["coordinates"]["T"] + request["coordinates"]["C"]
            vector[remaining[0]] = 1 if outcome["candidate"] == "valid" else 2
            candidates.append({"f": vector, "g": list(vector), "source": "fake_test_candidate",
                               "observed_monotonic": observed,
                               "public_validation": {"public_valid": True}})
        first_valid = ({"monotonic": observed, "source": "fake_test_candidate"} if candidates else None)
        status = outcome.get("worker_status", "completed")
        result = {
            "schema": "ntru-standalone-pattern-attempt-v1", "mode": "standalone_end_to_end",
            "method": request["method"], "trial_id": request["trial_id"],
            "pattern_attempt_index": request["pattern_attempt_index"],
            "coordinates": request["coordinates"], "seeds": request["seeds"],
            "config_sha256": request.get("config_sha256"),
            "status": status, "current_stage": "terminal",
            "stop_reason": "first_public_valid" if candidates else "candidate_list_exhausted",
            "candidates": candidates, "public_valid_observed": bool(candidates),
            "first_public_valid": first_valid, "progress": progress,
            "input_digests": {}, "timings": {"reduction_seconds": 0.2},
            "worker_work_deadline_met": observed <= request["work_deadline"],
            "finished_monotonic": observed, "protocol_errors": outcome.get("protocol_errors", []),
        }
        events = [{"event": "attempt_started", "sequence": 0, "monotonic": start,
                   "stage": "initialization", "method": request["method"],
                   "coordinates": request["coordinates"], "seeds": request["seeds"]}]
        for candidate in candidates:
            events.append({"event": "candidate", "sequence": len(events),
                           "monotonic": observed, "stage": "public_verify", **candidate})
        events.append({"event": "terminal", "sequence": len(events), "monotonic": observed,
                       "stage": "terminal", "status": status, "progress": progress,
                       "stop_reason": result["stop_reason"],
                       "public_valid_observed": bool(candidates), "input_digests": {}})
        event_path.write_text("".join(json.dumps(event) + "\n" for event in events))
        result_path.write_text(json.dumps(result))
        return {"execution_status": outcome.get("execution_status", "completed"),
                "cleanup_verified": outcome.get("cleanup_verified", True),
                "hard_deadline_met": observed <= request["hard_deadline"],
                "total_wall_seconds": observed - start,
                "work_seconds": observed - start, "cleanup_seconds": 0.0,
                "returncode": outcome.get("returncode", 0),
                "memory": {"metric_name": "test_unmeasured", "value": None,
                           "scope": "pure_fake_supervisor", "reason": "no real process"}}


class StandaloneRunnerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.public_path = self.directory / "public.json"
        self.public_path.write_text(json.dumps({
            "schema": "ntru-e2e-public-v1", "instance_id": "phase2d-owner-public-fixture",
            "n": 8, "q": 17, "ring": "x^n+1", "h": [1, 0, 0, 0, 0, 0, 0, 0],
            "distribution": {"kind": "fixed_weight_ternary", "w_f": 1, "w_g": 1}}))
        self.clock = Clock()
        self.config = {
            "schema": "ntru-standalone-trial-config-v1", "trial_budget_seconds": 10.0,
            "cleanup_reserve_seconds": 1.0, "pattern_limit_seconds": None,
            "max_pattern_attempts": 3, "k": 2, "kappa": 1, "pattern_seed": 123,
            "tail_rule": "largest_remaining", "minimum_attempt_work_seconds": 0.02,
            "worker": {"reduction_protocol": protocol_manifest(ReductionProtocol()),
                       "candidate_policy": {"mode": "full_nonzero"}, "backend_config": {},
                       "check_basis_rows": False, "zero_tail_policy": "skip",
                       "decode_stop_policy": "first_public_valid", "batch_size": 1,
                       "interface_test_only": False}}

    def run_fake(self, outcomes, *, config=None, method="E", trial_id="trial-0", total=30.0):
        supervisor = FakeSupervisor(self.clock, outcomes)
        result = run_trial(self.public_path, deepcopy(self.config if config is None else config),
                           method, trial_id, self.directory / trial_id,
                           experiment_started_at=100.0, total_budget_seconds=total,
                           supervisor=supervisor, clock=self.clock)
        return result, supervisor

    def test_failed_patterns_preserved_then_one_valid_trial_success(self):
        result, supervisor = self.run_fake([{}, {"candidate": "valid"}])
        self.assertEqual(len(supervisor.requests), 2)
        self.assertEqual(len(result["attempts"]), 2)
        self.assertTrue(result["public_valid_observed"])
        self.assertTrue(result["success_within_budget"])
        report = summarize_trials([result])
        self.assertEqual(report["budget_eligible_success_count"], 1)
        self.assertEqual(report["total_pattern_attempt_count"], 2)
        self.assertGreaterEqual(report["total_actual_wall_cost_seconds"], 1.0)

    def test_retries_inherit_trial_and_global_deadlines(self):
        result, supervisor = self.run_fake([{}, {}, {}], total=8.0)
        self.assertEqual(len(result["attempts"]), 3)
        self.assertEqual([r["pattern_attempt_index"] for r in supervisor.requests], [0, 1, 2])
        self.assertEqual({r["hard_deadline"] for r in supervisor.requests}, {108.0})
        self.assertEqual({r["work_deadline"] for r in supervisor.requests}, {107.0})
        self.assertEqual(result["total_wall_seconds"], 1.5)

    def test_zero_budget_does_not_spawn(self):
        config = deepcopy(self.config)
        config["trial_budget_seconds"] = 0.0
        result, supervisor = self.run_fake([], config=config)
        self.assertEqual(supervisor.requests, [])
        self.assertEqual(result["attempts"], [])
        self.assertFalse(result["success_within_budget"])

    def test_insufficient_remaining_budget_does_not_start_next_attempt(self):
        config = deepcopy(self.config)
        config.update(trial_budget_seconds=1.1, cleanup_reserve_seconds=0.2,
                      minimum_attempt_work_seconds=0.05)
        result, supervisor = self.run_fake([{"duration": 0.88}], config=config)
        self.assertEqual(len(supervisor.requests), 1)
        self.assertEqual(len(result["attempts"]), 1)
        self.assertFalse(result["success_within_budget"])

    def test_worker_public_valid_flag_cannot_validate_invalid_coefficients(self):
        config = deepcopy(self.config)
        config["max_pattern_attempts"] = 1
        result, _ = self.run_fake([{"candidate": "invalid"}], config=config)
        self.assertFalse(result["public_valid_observed"])
        self.assertFalse(result["success_within_budget"])
        self.assertEqual(summarize_trials([result])["budget_eligible_success_count"], 0)

    def test_public_valid_candidate_cannot_hide_nonzero_worker_exit(self):
        result, supervisor = self.run_fake([{"candidate": "valid", "returncode": 1,
                                            "execution_status": "worker_nonzero_exit"}])
        self.assertEqual(len(supervisor.requests), 1)
        self.assertTrue(result["public_valid_observed"])
        self.assertFalse(result["success_within_budget"])
        self.assertEqual(summarize_trials([result])["budget_eligible_success_count"], 0)

    def test_actual_supervisor_anomaly_statuses_are_retained_and_counted(self):
        for index, status in enumerate(("orphaned_descendants", "supervisor_interrupted")):
            with self.subTest(status=status):
                self.clock.now = 100.0
                result, supervisor = self.run_fake(
                    [{"candidate": "valid", "execution_status": status}],
                    trial_id="supervisor-anomaly-" + str(index))
                self.assertEqual(len(supervisor.requests), 1)
                self.assertEqual(result["execution_status"], status)
                self.assertTrue(result["public_valid_observed"])
                self.assertFalse(result["success_within_budget"])
                report = summarize_trials([result])
                self.assertEqual(report["exception_trial_count"], 1)
                self.assertEqual(report["budget_eligible_success_count"], 0)

    def test_cleanup_failure_stops_retries_and_disqualifies_valid_output(self):
        result, supervisor = self.run_fake([{"candidate": "valid", "cleanup_verified": False}])
        self.assertEqual(len(supervisor.requests), 1)
        self.assertFalse(result["cleanup_verified"])
        self.assertTrue(result["public_valid_observed"])
        self.assertFalse(result["success_within_budget"])
        report = summarize_trials([result])
        self.assertEqual(report["cleanup_failed_or_unverified_trial_count"], 1)
        self.assertEqual(report["budget_eligible_success_count"], 0)

    def test_supervisor_baseexception_marks_cleanup_unverified_and_stops(self):
        result, supervisor = self.run_fake([{"raise": KeyboardInterrupt("pure injected supervisor break")}])
        self.assertEqual(len(supervisor.requests), 1)
        self.assertEqual(len(result["attempts"]), 1)
        self.assertFalse(result["cleanup_verified"])
        self.assertFalse(result["success_within_budget"])
        evidence = result["attempts"][0]["supervision"]
        self.assertEqual(evidence["execution_status"], "cleanup_unverified")
        self.assertEqual(evidence["errors"][0]["type"], "KeyboardInterrupt")
        self.assertEqual(summarize_trials([result])["cleanup_failed_or_unverified_trial_count"], 1)

    def test_worker_protocol_error_disqualifies_valid_output_and_stops_retries(self):
        result, supervisor = self.run_fake([{"candidate": "valid",
                                            "protocol_errors": ["pure injected contradictory backend IDs"]}])
        self.assertEqual(len(supervisor.requests), 1)
        self.assertEqual(result["execution_status"], "protocol_error")
        self.assertTrue(result["public_valid_observed"])
        self.assertFalse(result["success_within_budget"])
        self.assertTrue(result["attempts"][0]["protocol_errors"])
        report = summarize_trials([result])
        self.assertEqual(report["exception_trial_count"], 1)
        self.assertEqual(report["budget_eligible_success_count"], 0)

    def test_experiment_never_starts_next_trial_after_unverified_cleanup(self):
        plans = [{"public_path": str(self.public_path), "config": deepcopy(self.config),
                  "method": "E", "trial_id": "cleanup-failed-first"},
                 {"public_path": str(self.public_path), "config": deepcopy(self.config),
                  "method": "D", "trial_id": "must-not-start-second"}]

        def fake_trial(public_path, config, method, trial_id, output_dir, **kwargs):
            self.clock.now += 0.2
            return {"trial_id": trial_id, "method": method, "instance_id": "fake-owner-test",
                    "started": True, "cost_mode": "standalone_end_to_end", "attempts": [{}],
                    "execution_status": "cleanup_failed", "cleanup_verified": False,
                    "total_wall_seconds": 0.2, "public_valid_observed": False,
                    "success_within_budget": False, "eligible_for_primary_comparison": False,
                    "deadlines_satisfied": True, "supervisor_public_validation": None}

        with patch("experiments.ntru_hybrid_comparison.standalone.run_trial", side_effect=fake_trial) as mock:
            result = run_experiment(plans, self.directory / "experiment-cleanup-failure",
                                    total_budget_seconds=30, experiment_started_at=100,
                                    clock=self.clock)
        self.assertEqual(mock.call_count, 1)
        self.assertEqual(len(result["trials"]), 2)
        self.assertFalse(result["trials"][1]["started"])
        self.assertEqual(result["trials"][1]["stop_reason"], "not_started_after_cleanup_failure")
        self.assertEqual(result["summary"]["started_trial_count"], 1)
        self.assertEqual(result["summary"]["not_started_planned_trial_count"], 1)
        self.assertEqual(result["summary"]["cleanup_failed_or_unverified_trial_count"], 1)
        self.assertAlmostEqual(result["summary"]["total_actual_wall_cost_seconds"], 0.2)

    def test_late_valid_output_preserved_but_not_budget_success(self):
        result, supervisor = self.run_fake([{"candidate": "valid", "late_candidate": True}])
        self.assertEqual(len(supervisor.requests), 1)
        self.assertTrue(result["public_valid_observed"])
        self.assertFalse(result["success_within_budget"])
        self.assertEqual(summarize_trials([result])["budget_eligible_success_count"], 0)

    def test_d_e_public_sequence_reproducible_while_costs_are_independent(self):
        config = deepcopy(self.config)
        config["max_pattern_attempts"] = 2
        result_d, supervisor_d = self.run_fake([{}, {}], config=config, method="D", trial_id="D-0")
        self.clock.now = 100.0
        result_e, supervisor_e = self.run_fake([{}, {}], config=config, method="E", trial_id="E-0")
        self.assertEqual([r["coordinates"] for r in supervisor_d.requests],
                         [r["coordinates"] for r in supervisor_e.requests])
        for request_d, request_e in zip(supervisor_d.requests, supervisor_e.requests):
            self.assertEqual(request_d["seeds"], request_e["seeds"])
        self.assertEqual(result_d["total_wall_seconds"], 1.0)
        self.assertEqual(result_e["total_wall_seconds"], 1.0)
        report = summarize_trials([result_d, result_e])
        self.assertEqual(report["total_actual_wall_cost_seconds"], 2.0)
        self.assertEqual(report["total_pattern_attempt_count"], 4)


if __name__ == "__main__":
    unittest.main()
