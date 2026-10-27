"""Cold-attempt orchestration with simulated reduction/decoding, never lattices.

All backend calls below are pure mocks. Actual backend acceptance is a separate
bounded stage-2D artifact and cannot be inferred from these tests.
"""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiments.ntru_hybrid_comparison import standalone_worker as worker
from experiments.ntru_hybrid_comparison.reduction import ReductionProtocol, protocol_manifest


class SimulatedClock:
    def __init__(self):
        self.now = 10.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def public_fixture():
    return {"schema": "ntru-e2e-public-v1", "instance_id": "phase2d-pure-worker-test",
            "n": 4, "q": 17, "ring": "x^n+1", "h": [1, 0, 0, 0],
            "distribution": {"kind": "fixed_weight_ternary", "w_f": 1, "w_g": 1}}


def fake_reduction(rows, protocol):
    return {"original_basis": deepcopy(rows), "reduced_basis": deepcopy(rows),
            "transform": [[int(i == j) for j in range(len(rows))] for i in range(len(rows))],
            "protocol": protocol_manifest(protocol), "lattice_status": "reduced_verified",
            "record": {"status": "completed", "mock_only_not_backend_measurement": True}}


def fake_attach(bundle, execution):
    result = deepcopy(bundle)
    result["basis"]["reduced"] = deepcopy(execution["reduced_basis"])
    return result


def fake_copy(bundle):
    return {"rows": deepcopy(bundle["basis"]["reduced"]), "gso": None}


class MockBackendFactory:
    def __init__(self, callback=None):
        self.instances = []
        self.callback = callback

    def __call__(self, working, method, config):
        owner = self

        class Backend:
            def __init__(self):
                self.rows = working["rows"]
                self.metadata = {"mock_only": True, "hidden_reduction_calls": 0}
                self.metrics, self.call_evidence = {}, []
                self.preprocess_calls, self.calls = 0, []
                self.method, self.config = method, config

            def preprocess(self):
                self.preprocess_calls += 1

            def decode(self, targets, bounds, ids):
                self.calls.append((deepcopy(targets), list(bounds), list(ids)))
                if owner.callback:
                    return owner.callback(self, targets, bounds, ids)
                return {"candidates": [{"backend_integer_id": i, "v": [0] * len(self.rows),
                                        "output_origin": "babai_only" if method == "E" else "slicer_tail_with_babai_lift"}
                                       for i in ids], "completed_ids": list(ids),
                        "errored_ids": [], "cancelled_ids": []}

        backend = Backend()
        self.instances.append(backend)
        return backend


class StandaloneWorkerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.public_path = self.path / "public.json"
        self.public_path.write_text(json.dumps(public_fixture()))
        self.clock = SimulatedClock()
        self.request = {
            "schema": worker.SCHEMA, "public_path": str(self.public_path), "method": "E",
            "trial_id": "mock-trial-0", "pattern_attempt_index": 0,
            "coordinates": {"P": [3], "C": [1, 2], "T": [0]},
            "seeds": {"pattern_seed": 1, "tail_seed": 2, "candidate_seed": 3,
                      "reduction_seed": 10001, "decoder_seed": 4},
            "budget_origin_monotonic": 0.0, "work_deadline": 30.0, "hard_deadline": 32.0,
            "config": {"reduction_protocol": protocol_manifest(ReductionProtocol()),
                       "candidate_policy": {"mode": "full_nonzero"},
                       "backend_config": {}, "check_basis_rows": False,
                       "zero_tail_policy": "skip", "decode_stop_policy": "first_public_valid", "batch_size": 1},
        }

    def run_mock(self, request=None, *, reducer=None, backend=None, durable=False):
        with patch.object(worker, "attach_reduction", side_effect=fake_attach), patch.object(worker, "create_working_copy", side_effect=fake_copy):
            return worker.run_attempt(self.request if request is None else request, clock=self.clock,
                                      reduction_executor=reducer or fake_reduction,
                                      backend_factory=backend or MockBackendFactory(),
                                      events_path=self.path / "events.jsonl" if durable else None,
                                      result_path=self.path / "result.json" if durable else None)

    def test_cold_e_calls_reduction_and_preprocess_once_stops_public_valid(self):
        reductions = []

        def reduce(rows, protocol):
            reductions.append(deepcopy(rows))
            return fake_reduction(rows, protocol)

        backend = MockBackendFactory()
        result = self.run_mock(reducer=reduce, backend=backend)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["mode"], "standalone_end_to_end")
        self.assertEqual(result["stop_reason"], "first_public_valid")
        self.assertEqual(len(reductions), 1)
        self.assertEqual(backend.instances[0].preprocess_calls, 1)
        self.assertEqual(result["progress"]["submitted_count"], 1)
        self.assertEqual(result["progress"]["completed_count"], 1)
        self.assertEqual(result["progress"]["unsubmitted_count"], 1)
        self.assertTrue(result["public_valid_observed"])
        self.assertFalse(result["cached_reduction_used"])
        self.assertFalse(result["cached_database_used"])
        self.assertIsNone(result["cleanup_verified"])

    def test_b_primal_recovers_nonzero_basis_row_without_decoder_or_targets(self):
        request = deepcopy(self.request)
        request["method"] = "B"
        request["coordinates"] = {"P": [3], "C": [0, 1, 2], "T": []}
        request["config"]["check_basis_rows"] = True
        backend = MockBackendFactory()
        result = self.run_mock(request, backend=backend)
        self.assertEqual(result["stop_reason"], "first_public_valid_basis_row")
        self.assertEqual(result["first_public_valid"]["source"], "basis_row")
        self.assertEqual(result["dimensions"]["d"], 7)
        self.assertEqual(result["progress"]["target_count"], 0)
        self.assertIsNone(result["input_digests"]["ordered_targets_sha256"])
        self.assertEqual(backend.instances, [])

    def test_d_reduction_row_success_does_not_force_slicer(self):
        request = deepcopy(self.request)
        request["method"] = "D"
        request["config"]["check_basis_rows"] = True
        backend = MockBackendFactory()
        result = self.run_mock(request, backend=backend)
        self.assertEqual(result["stop_reason"], "first_public_valid_basis_row")
        self.assertEqual(result["progress"]["submitted_count"], 0)
        self.assertEqual(backend.instances, [])

    def test_independent_d_e_generate_same_public_inputs_but_pay_two_reductions(self):
        calls = []

        def reduce(rows, protocol):
            calls.append(rows)
            self.clock.advance(2)
            return fake_reduction(rows, protocol)

        backend = MockBackendFactory()
        result_e = self.run_mock(reducer=reduce, backend=backend)
        request = deepcopy(self.request)
        request["method"] = "D"
        result_d = self.run_mock(request, reducer=reduce, backend=backend)
        self.assertEqual(result_e["input_digests"], result_d["input_digests"])
        self.assertEqual(len(calls), 2)
        self.assertIsNot(calls[0], calls[1])
        self.assertEqual(result_e["timings"]["reduction_seconds"], 2)
        self.assertEqual(result_d["timings"]["reduction_seconds"], 2)
        self.assertIsNot(backend.instances[0].rows, backend.instances[1].rows)

    def test_absolute_budget_already_spent_before_worker_no_load_or_reduction(self):
        request = deepcopy(self.request)
        request["work_deadline"] = 9.0
        called = []
        result = self.run_mock(request, reducer=lambda *args: called.append(args))
        self.assertEqual(result["status"], "work_deadline")
        self.assertEqual(result["current_stage"], "terminal")
        self.assertNotIn("instance_id", result)
        self.assertEqual(called, [])

    def test_reduction_time_reduces_decode_budget_no_stage_restart(self):
        request = deepcopy(self.request)
        request["work_deadline"] = 12.0

        def reduce(rows, protocol):
            self.clock.advance(3.0)
            return fake_reduction(rows, protocol)

        backend = MockBackendFactory()
        result = self.run_mock(request, reducer=reduce, backend=backend)
        self.assertEqual(result["status"], "work_deadline")
        self.assertEqual(result["timings"]["reduction_seconds"], 3.0)
        self.assertEqual(result["work_deadline"], 12.0)
        self.assertEqual(backend.instances, [])
        self.assertEqual(result["progress"]["submitted_count"], 0)

    def test_missing_terminal_result_stays_pending_never_completed(self):
        backend = MockBackendFactory(lambda *args: {"candidates": [], "completed_ids": [], "errored_ids": [], "cancelled_ids": []})
        result = self.run_mock(backend=backend)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["progress"]["pending_count"], 2)
        self.assertEqual(result["progress"]["completed_count"], 0)
        self.assertTrue(result["progress"]["conservation_holds"])

    def test_backend_exception_records_observed_error_not_completed(self):
        def fail(*args):
            raise RuntimeError("explicit test exception")

        result = self.run_mock(backend=MockBackendFactory(fail))
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["progress"]["submitted_count"], 1)
        self.assertEqual(result["progress"]["errored_count"], 1)
        self.assertEqual(result["progress"]["completed_count"], 0)
        self.assertEqual(result["progress"]["unsubmitted_count"], 1)

    def test_deadline_after_decode_preserves_pending_no_false_completion(self):
        def late(backend, targets, bounds, ids):
            self.clock.advance(21)
            return {"candidates": [{"backend_integer_id": ids[0], "v": [0] * len(backend.rows)}],
                    "completed_ids": list(ids), "errored_ids": [], "cancelled_ids": []}

        result = self.run_mock(backend=MockBackendFactory(late))
        self.assertEqual(result["status"], "work_deadline")
        self.assertFalse(result["public_valid_observed"])
        self.assertEqual(result["progress"]["pending_count"], 1)
        self.assertEqual(result["progress"]["completed_count"], 0)
        self.assertEqual(len(result["backend_batches"]), 1)

    def test_durable_events_and_checkpoint_are_public_and_conserve_progress(self):
        result = self.run_mock(durable=True)
        records = [json.loads(line) for line in (self.path / "events.jsonl").read_text().splitlines()]
        checkpoint = json.loads((self.path / "result.json").read_text())
        self.assertEqual(records[0]["event"], "attempt_started")
        self.assertEqual(records[-1]["event"], "terminal")
        self.assertEqual(records[-1]["progress"], result["progress"])
        self.assertEqual(checkpoint["progress"], result["progress"])
        self.assertEqual([row["sequence"] for row in records], list(range(len(records))))
        events = [row["event"] for row in records]
        self.assertLess(events.index("target_submitted"), events.index("candidate"))
        self.assertLess(events.index("candidate"), events.index("target_completed"))
        candidate = next(row for row in records if row["event"] == "candidate")
        self.assertEqual(candidate["f"], result["candidates"][0]["f"])
        self.assertEqual(candidate["monotonic"], self.clock())
        self.assertNotIn("reference", json.dumps(records))
        with self.assertRaises(FileExistsError):
            self.run_mock(durable=True)

    def test_test_only_backend_options_never_implicitly_enabled(self):
        for options in ({"force_kernel_iterations": True}, {"preprocessing": "sample_database_only"},
                        {"interface_test_mode": True}):
            request = deepcopy(self.request)
            request["method"] = "D"
            request["config"]["backend_config"] = options
            with self.assertRaises(ValueError):
                worker.validate_request(request)
            request["config"]["interface_test_only"] = True
            worker.validate_request(request)

    def test_rejects_reference_keys_cached_inputs_zero_tail_and_seed_mismatch(self):
        cases = []
        request = deepcopy(self.request)
        request["reference_path"] = "never-read"
        cases.append(request)
        request = deepcopy(self.request)
        request["config"]["cached_basis"] = "never-load"
        cases.append(request)
        request = deepcopy(self.request)
        request["config"]["zero_tail_policy"] = "recover_free"
        cases.append(request)
        request = deepcopy(self.request)
        request["config"]["backend_config"]["seed"] = 19
        cases.append(request)
        request = deepcopy(self.request)
        request["seeds"]["reduction_seed"] = 3
        cases.append(request)
        for request in cases:
            with self.subTest(request=request), self.assertRaises(ValueError):
                worker.validate_request(request)

    def test_unknown_target_or_hidden_basis_change_is_run_error(self):
        def unknown(backend, targets, bounds, ids):
            return {"candidates": [], "completed_ids": [999]}

        result = self.run_mock(backend=MockBackendFactory(unknown))
        self.assertEqual(result["status"], "error")
        self.assertIn("terminal ID", result["error"]["message"])

    def test_public_constraints_reject_reconstruction_despite_backend_claim(self):
        def fake_success(backend, targets, bounds, ids):
            # h=1, but nonzero coefficient 2 gives nonternary reconstructed g.
            return {"candidates": [{"backend_integer_id": ids[0], "v": [17, 0, 0, 0, 0, 0],
                                    "public_valid": True, "output_origin": "mock"}],
                    "completed_ids": list(ids)}

        result = self.run_mock(backend=MockBackendFactory(fake_success))
        self.assertFalse(result["public_valid_observed"])
        self.assertEqual(result["progress"]["completed_count"], 2)
        self.assertEqual(result["candidates"][0]["public_validation"]["public_valid"], False)


if __name__ == "__main__":
    unittest.main()
