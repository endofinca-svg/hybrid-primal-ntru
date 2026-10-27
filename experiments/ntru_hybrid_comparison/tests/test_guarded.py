"""Pure tests for the external guard and killed-owner evidence recovery.

No process or lattice backend is started. Durable owner and worker records are
explicit fixtures; the fake guard cannot turn worker flags into validation.
"""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from experiments.ntru_hybrid_comparison.inputs import content_sha256
from experiments.ntru_hybrid_comparison.reduction import ReductionProtocol, protocol_manifest
from experiments.ntru_hybrid_comparison.summarize import summarize_trials


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def config_fixture():
    return {"schema": "ntru-standalone-trial-config-v1", "trial_budget_seconds": 10.0,
            "cleanup_reserve_seconds": 1.0, "pattern_limit_seconds": None,
            "max_pattern_attempts": 2, "k": 1, "kappa": 1, "pattern_seed": 3,
            "tail_rule": "largest_remaining", "minimum_attempt_work_seconds": .02,
            "worker": {"reduction_protocol": protocol_manifest(ReductionProtocol()),
                       "candidate_policy": {"mode": "full_nonzero"}, "backend_config": {},
                       "check_basis_rows": False, "zero_tail_policy": "skip",
                       "decode_stop_policy": "first_public_valid", "batch_size": 1,
                       "interface_test_only": False}}


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n")


def write_events(path, records):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in records))


def complete_owner_record(config, *, valid=True):
    return {"schema": "ntru-independent-trial-v1", "method": "E", "trial_id": "guard-test",
            "instance_id": "guard-public-fixture", "config_sha256": content_sha256(config),
            "config": deepcopy(config), "cost_mode": "standalone_end_to_end", "started": True,
            "attempts": [{"pattern_attempt_index": 0, "execution_status": "completed",
                          "cleanup_verified": True, "total_wall_seconds": .15}],
            "public_valid_observed": valid, "success_within_budget": valid,
            "execution_status": "completed", "cleanup_verified": True,
            "eligible_for_primary_comparison": True,
            "supervisor_public_validation": {"public_valid": valid},
            "deadlines_satisfied": True, "stop_reason": "first_public_valid_within_budget" if valid else "maximum_pattern_attempts_reached",
            "total_wall_seconds": .2, "cleanup_seconds": .01,
            "first_public_valid": {"source": "owner_verified_test_fixture"} if valid else None,
            "memory": {"metric_name": "inner_test_memory", "value": 111, "unit": "bytes"}}


class FakeGuardSupervisor:
    def __init__(self, clock, config, *, outcome=None, owner_record=None, write_partial=None):
        self.clock, self.config = clock, config
        self.outcome = {} if outcome is None else outcome
        self.owner_record = owner_record
        self.write_partial = write_partial
        self.calls = []
        self.requests = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), deepcopy(kwargs)))
        request = json.loads(Path(argv[argv.index("--owner-request") + 1]).read_text())
        self.requests.append(deepcopy(request))
        directory = Path(request["owner_output_dir"])
        directory.mkdir(parents=True, exist_ok=True)
        if self.owner_record is not None:
            write_json(directory / "trial.final.json", self.owner_record)
        if self.write_partial is not None:
            self.write_partial(directory)
        began = self.clock()
        self.clock.now += .5
        status = self.outcome.get("execution_status", "completed")
        return {"schema": "ntru-trial-supervision-v1", "execution_status": status,
                "execution_status_before_cleanup": status,
                "returncode": self.outcome.get("returncode", 0),
                "cleanup_verified": self.outcome.get("cleanup_verified", True),
                "hard_deadline_met": self.outcome.get("hard_deadline_met", True),
                "run_success": status == "completed",
                "started_monotonic": began, "finished_monotonic": self.clock(),
                "total_wall_seconds": .5, "work_seconds": .4, "cleanup_seconds": .1,
                "termination_reason": "pure_test_fake_guard",
                "memory": {"metric_name": "sampled_simultaneous_process_tree_rss_bytes",
                           "scope": "guard session including owner and worker descendants; excludes guard",
                           "value": 222, "unit": "bytes", "sampling_estimate": True,
                           "includes_supervisor": False, "covers_descendants": True}}


class GuardedOwnerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)
        self.clock, self.config = Clock(), config_fixture()
        self.public_path = self.path / "public.json"
        write_json(self.public_path, {"schema": "ntru-e2e-public-v1", "instance_id": "guard-public-fixture",
                                    "n": 4, "q": 17, "ring": "x^n+1", "h": [1, 0, 0, 0],
                                    "distribution": {"kind": "fixed_weight_ternary", "w_f": 1, "w_g": 1}})

    def guarded(self, outcome=None, record=None, partial=None, *, name="guard"):
        from experiments.ntru_hybrid_comparison.guarded import run_guarded_trial
        supervisor = FakeGuardSupervisor(self.clock, self.config, outcome=outcome,
                                         owner_record=complete_owner_record(self.config) if record is None and partial is None else record,
                                         write_partial=partial)
        result = run_guarded_trial(self.public_path, self.config, "E", "guard-test", self.path / name,
                                   experiment_started_at=100.0, total_budget_seconds=30.0,
                                   guard_cleanup_reserve_seconds=.2, supervisor=supervisor, clock=self.clock)
        return result, supervisor

    def prepared_partial(self, directory, *, candidate=True, completed=False):
        prepared = {"pattern_attempt_index": 0, "trial_id": "guard-test", "method": "E",
                    "instance_id": "guard-public-fixture", "coordinates": {"P": [1], "C": [0, 2], "T": [3]},
                    "seeds": {"pattern_seed": 3, "tail_seed": 0, "candidate_seed": 0,
                              "reduction_seed": 10001, "decoder_seed": 10001},
                    "config_sha256": content_sha256(self.config), "started_monotonic": 100.1}
        write_events(directory / "owner_online.jsonl", [
            {"event": "trial_started", "monotonic": 100.0, "trial_id": "guard-test", "method": "E",
             "config_sha256": content_sha256(self.config)},
            {"event": "pattern_prepared", "monotonic": 100.1, **prepared},
        ])
        attempt = directory / "attempt_000"
        write_json(attempt / "request.json", {"method": "E", "trial_id": "guard-test",
                                                "pattern_attempt_index": 0, "coordinates": prepared["coordinates"],
                                                "seeds": prepared["seeds"], "config_sha256": prepared["config_sha256"]})
        worker_events = [{"event": "attempt_started", "sequence": 0, "monotonic": 100.11, "stage": "initialization"},
                         {"event": "target_submitted", "sequence": 1, "monotonic": 100.2, "stage": "decode", "external_id": 0}]
        if candidate:
            worker_events.append({"event": "candidate", "sequence": len(worker_events), "monotonic": 100.3,
                                  "observed_monotonic": 100.3, "stage": "public_verify", "external_id": 0,
                                  "f": [1, 0, 0, 0], "g": [1, 0, 0, 0],
                                  "source": "unvalidated_worker_test_flag", "public_validation": {"public_valid": True}})
        if completed:
            worker_events.append({"event": "target_completed", "sequence": len(worker_events), "monotonic": 100.31,
                                  "stage": "decode", "external_id": 0})
        write_events(attempt / "events.jsonl", worker_events)
        write_json(attempt / "result.json", {"status": "running", "public_valid_observed": True,
                                               "progress": {"target_count": 1,
                                                            "targets": [{"external_id": 0, "state": "pending"}]}})

    def test_recover_killed_owner_retains_pending_and_unvalidated_candidate(self):
        from experiments.ntru_hybrid_comparison.guarded import recover_owner_progress
        directory = self.path / "partial"
        self.prepared_partial(directory)
        result = recover_owner_progress(directory, method="E", trial_id="guard-test", config=self.config)
        self.assertEqual(len(result["attempts"]), 1)
        self.assertEqual(result["attempts"][0]["progress"]["pending_count"], 1)
        self.assertEqual(result["attempts"][0]["progress"]["completed_count"], 0)
        self.assertFalse(result["success_within_budget"])
        self.assertFalse(result["public_valid_observed"])
        self.assertTrue(result["attempts"][0]["progress"]["conservation_holds"])
        self.assertTrue(result["attempts"][0]["unvalidated_candidate_events"])

    def test_recover_explicit_completion_retained_without_inventing_success(self):
        from experiments.ntru_hybrid_comparison.guarded import recover_owner_progress
        directory = self.path / "completed-target"
        self.prepared_partial(directory, completed=True)
        result = recover_owner_progress(directory, method="E", trial_id="guard-test", config=self.config)
        self.assertEqual(result["attempts"][0]["progress"]["completed_count"], 1)
        self.assertEqual(result["attempts"][0]["progress"]["pending_count"], 0)
        self.assertFalse(result["success_within_budget"])
        self.assertFalse(result["public_valid_observed"])

    def test_recovery_keeps_prepared_attempt_when_request_write_was_interrupted(self):
        from experiments.ntru_hybrid_comparison.guarded import recover_owner_progress
        directory = self.path / "truncated-request"
        self.prepared_partial(directory, candidate=False)
        (directory / "attempt_000/request.json").write_text('{"method":"E",')
        result = recover_owner_progress(directory, method="E", trial_id="guard-test", config=self.config)
        self.assertEqual(len(result["attempts"]), 1)
        self.assertEqual(result["attempts"][0]["progress"]["pending_count"], 1)
        self.assertFalse(result["success_within_budget"])
        self.assertFalse(result["public_valid_observed"])

    def test_normal_owner_result_uses_complete_guard_wall_and_outer_memory(self):
        result, supervisor = self.guarded()
        self.assertEqual(len(supervisor.calls), 1)
        self.assertTrue(result["success_within_budget"])
        self.assertTrue(result["eligible_for_primary_comparison"])
        self.assertEqual(result["total_wall_seconds"], .5)
        self.assertEqual(result["memory"]["value"], 222)
        self.assertEqual(result["owner_diagnostics"]["total_wall_seconds"], .2)
        self.assertEqual(summarize_trials([result])["budget_eligible_success_count"], 1)

    def test_outer_timeout_cannot_be_hidden_by_successful_owner_snapshot(self):
        result, _ = self.guarded({"execution_status": "timeout", "returncode": -15})
        self.assertTrue(result["public_valid_observed"])
        self.assertFalse(result["success_within_budget"])
        self.assertEqual(result["execution_status"], "timeout")
        self.assertEqual(result["total_wall_seconds"], .5)
        self.assertEqual(summarize_trials([result])["budget_eligible_success_count"], 0)

    def test_outer_nonzero_orphan_or_exception_keeps_abnormal_fact(self):
        for index, status in enumerate(("worker_nonzero_exit", "orphaned_descendants", "supervisor_exception")):
            with self.subTest(status=status):
                result, _ = self.guarded({"execution_status": status, "returncode": 1}, name=f"abnormal-{index}")
                self.assertFalse(result["success_within_budget"])
                self.assertFalse(result["eligible_for_primary_comparison"])
                self.assertEqual(result["execution_status"], status)
                self.assertTrue(result["cleanup_verified"])

    def test_cleanup_failure_disqualifies_even_owner_success(self):
        result, _ = self.guarded({"cleanup_verified": False, "execution_status": "cleanup_unverified"})
        self.assertFalse(result["cleanup_verified"])
        self.assertFalse(result["success_within_budget"])
        self.assertFalse(result["eligible_for_primary_comparison"])
        self.assertEqual(summarize_trials([result])["cleanup_failed_or_unverified_trial_count"], 1)

    def test_outer_hard_deadline_failure_disqualifies_success(self):
        result, _ = self.guarded({"hard_deadline_met": False})
        self.assertFalse(result["success_within_budget"])
        self.assertFalse(result["eligible_for_primary_comparison"])
        self.assertFalse(result["deadlines_satisfied"])

    def test_missing_owner_final_recovers_attempt_and_counts_guard_cost(self):
        result, _ = self.guarded({"execution_status": "timeout", "returncode": -15}, partial=self.prepared_partial)
        self.assertTrue(result["started"])
        self.assertEqual(len(result["attempts"]), 1)
        self.assertEqual(result["attempts"][0]["progress"]["pending_count"], 1)
        self.assertEqual(result["total_wall_seconds"], .5)
        self.assertFalse(result["public_valid_observed"])
        self.assertFalse(result["success_within_budget"])
        summary = summarize_trials([result])
        self.assertEqual(summary["total_pattern_attempt_count"], 1)
        self.assertEqual(summary["cost_per_budget_eligible_success_seconds"], None)

    def test_owner_binding_mismatch_rejected_as_protocol_failure(self):
        owner = complete_owner_record(self.config)
        owner["trial_id"] = "wrong-trial"
        result, _ = self.guarded(record=owner)
        self.assertFalse(result["success_within_budget"])
        self.assertFalse(result["eligible_for_primary_comparison"])
        self.assertEqual(result["execution_status"], "protocol_error")

    def test_guard_passes_original_trial_start_and_reserves_within_hard_bound(self):
        result, supervisor = self.guarded()
        self.assertEqual(supervisor.requests[0]["trial_started_at"], 100.0)
        self.assertEqual(supervisor.calls[0][1]["hard_deadline"], 110.0)
        self.assertEqual(supervisor.calls[0][1]["work_deadline"], 109.8)
        self.assertEqual(result["budget"]["cleanup_reserve_seconds"], 1.0)
        self.assertEqual(result["guard_cleanup_reserve_seconds"], .2)

    def test_delayed_owner_start_inherits_trial_deadlines_without_reset(self):
        from experiments.ntru_hybrid_comparison.standalone import run_trial
        from experiments.ntru_hybrid_comparison.tests.test_standalone_runner import FakeSupervisor
        self.clock.now = 102.0
        config = deepcopy(self.config)
        config["max_pattern_attempts"] = 1
        supervisor = FakeSupervisor(self.clock, [{}])
        result = run_trial(self.public_path, config, "E", "delayed-owner", self.path / "delayed-owner",
                           experiment_started_at=100.0, total_budget_seconds=30.0,
                           trial_started_at=100.0, supervisor=supervisor, clock=self.clock)
        self.assertEqual(supervisor.requests[0]["budget_origin_monotonic"], 100.0)
        self.assertEqual(supervisor.requests[0]["hard_deadline"], 110.0)
        self.assertEqual(supervisor.requests[0]["work_deadline"], 109.0)
        self.assertEqual(result["started_monotonic"], 100.0)
        self.assertEqual(result["total_wall_seconds"], 2.5)


if __name__ == "__main__":
    unittest.main()
