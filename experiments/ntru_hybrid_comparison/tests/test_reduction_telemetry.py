"""Pure fake-backend telemetry checks, never real LLL/BKZ or decoding."""

from copy import deepcopy
from contextlib import contextmanager
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

from experiments.ntru_hybrid_comparison import reduction, standalone_worker as worker
from experiments.ntru_hybrid_comparison.tests.test_reduction import completed_fixture
from experiments.ntru_hybrid_comparison.tests.test_standalone_worker import (
    SimulatedClock, fake_reduction, public_fixture,
)


@contextmanager
def fake_backend(protocol, *, fail_tour=None):
    """Inert matrices and calls: preserve all integers without reducing them."""
    class Matrix:
        def __init__(self, rows):
            self.rows = deepcopy(rows)
            self.nrows, self.ncols = len(rows), len(rows[0])

        def __getitem__(self, index):
            return self.rows[index[0]][index[1]]

        @staticmethod
        def from_matrix(rows, **kwargs):
            return Matrix(rows)

    state = SimpleNamespace(precision=53, threads=1, tours=0)
    fplll = SimpleNamespace(set_threads=lambda v: setattr(state, "threads", v),
                           set_random_seed=lambda v: None,
                           set_precision=lambda v: setattr(state, "precision", v),
                           get_threads=lambda: state.threads, get_precision=lambda: state.precision)

    class GSO:
        def __init__(self, matrix, **kwargs):
            self.float_type = kwargs["float_type"]

        def update_gso(self):
            pass

    class Reducer:
        def __init__(self, gso):
            pass

        def tour(self, params):
            state.tours += 1
            if state.tours == fail_tour:
                raise RuntimeError("explicit fake tour fault; no backend computation")
            return True

    top = ModuleType("fpylll")
    top.FPLLL, top.IntegerMatrix = fplll, Matrix
    top.GSO = SimpleNamespace(Mat=GSO)
    top.LLL = SimpleNamespace(reduction=lambda *args, **kwargs: None,
                              Reduction=lambda gso, **kwargs: gso)
    top.BKZ = SimpleNamespace(Param=lambda beta, **kwargs: SimpleNamespace(beta=beta))
    bkz2, param = ModuleType("fpylll.algorithms.bkz2"), ModuleType("fpylll.fplll.bkz_param")
    bkz2.BKZReduction = Reducer
    param.Strategy = lambda *args: object()
    param.load_strategies_json = lambda path: [object() for _ in range(5)]
    metadata = completed_fixture(protocol)["backend"]
    with patch.dict(sys.modules, {"fpylll": top, "fpylll.algorithms.bkz2": bkz2,
                                  "fpylll.fplll.bkz_param": param}), patch.object(
                                      reduction, "backend_metadata", return_value=metadata):
        yield state


class ReductionTelemetryTests(unittest.TestCase):
    def setUp(self):
        self.protocol = reduction.ReductionProtocol(schedule=(reduction.ReductionStage(2, 2),))
        self.original = [[2, 0], [1, 1]]

    def test_completed_events_are_exact_observed_prefix_with_no_basis_rows(self):
        events = []
        with fake_backend(self.protocol):
            result = reduction.execute_reduction(self.original, self.protocol, event_sink=events.append)
        self.assertEqual(result["record"]["status"], "completed")
        self.assertEqual([e["reduction_event"] for e in events],
                         ["initialization_started", "initialization_completed",
                          "stage_started", "stage_completed", "stage_started", "stage_completed",
                          "stage_started", "stage_completed", "proof_started", "proof_completed", "executor_finished"])
        completed = [e for e in events if e["reduction_event"] == "stage_completed"]
        self.assertEqual([e["reduction_record"]["actual"]["completed_tours"] for e in completed], [0, 1, 2])
        self.assertEqual([len(e["reduction_record"]["stages"]) for e in completed], [1, 2, 3])
        for event in events:
            self.assertIsInstance(event["observed_monotonic"], float)
            self.assertGreaterEqual(event["executor_elapsed_seconds"], 0)
            self.assertNotIn("original_basis", event)
            self.assertNotIn("reduced_basis", event)
        self.assertEqual(sorted(e["observed_monotonic"] for e in events), [e["observed_monotonic"] for e in events])
        events[-1]["reduction_record"]["actual"]["completed_tours"] = 999
        self.assertEqual(result["record"]["actual"]["completed_tours"], 2)

    def test_failed_tour_never_gets_a_completion_event(self):
        events = []
        with fake_backend(self.protocol, fail_tour=2):
            result = reduction.execute_reduction(self.original, self.protocol, event_sink=events.append)
        self.assertEqual(result["record"]["status"], "error")
        self.assertEqual(result["record"]["actual"]["completed_tours"], 1)
        self.assertEqual(len([e for e in events if e["reduction_event"] == "stage_started"]), 3)
        self.assertEqual(len([e for e in events if e["reduction_event"] == "stage_completed"]), 2)
        self.assertEqual(events[-1]["reduction_record"]["actual"]["completed_tours"], 1)

    def test_initialization_fault_claims_no_completed_work(self):
        events = []
        with patch.object(reduction, "backend_metadata", side_effect=RuntimeError("fake unavailable backend")):
            result = reduction.execute_reduction(self.original, self.protocol, event_sink=events.append)
        self.assertEqual([e["reduction_event"] for e in events],
                         ["initialization_started", "proof_started", "proof_completed", "executor_finished"])
        self.assertEqual(result["record"]["actual"]["completed_tours"], 0)
        self.assertEqual(result["record"]["actual"]["completed_lll_calls"], 0)

    def test_no_sink_retains_original_inert_execution(self):
        with fake_backend(self.protocol):
            without = reduction.execute_reduction(self.original, self.protocol)
        with fake_backend(self.protocol):
            with_sink = reduction.execute_reduction(self.original, self.protocol, event_sink=lambda event: None)
        for key in ("original_basis", "reduced_basis", "transform", "lattice_status"):
            self.assertEqual(without[key], with_sink[key])
        self.assertEqual(without["record"]["actual"], with_sink["record"]["actual"])


class WorkerTelemetryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        public = self.directory / "public.json"
        public.write_text(json.dumps(public_fixture()))
        self.clock = SimulatedClock()
        self.request = {"schema": worker.SCHEMA, "public_path": str(public), "method": "B",
                        "trial_id": "telemetry-pure-test", "pattern_attempt_index": 0,
                        "coordinates": {"P": [3], "C": [0, 1, 2], "T": []},
                        "seeds": {"reduction_seed": 10001}, "budget_origin_monotonic": 0.0,
                        "work_deadline": 30.0, "hard_deadline": 32.0,
                        "config": {"reduction_protocol": reduction.protocol_manifest(reduction.ReductionProtocol()),
                                   "check_basis_rows": True, "zero_tail_policy": "skip"}}

    def snapshot(self):
        return {"reduction_event": "stage_started", "observed_monotonic": self.clock(),
                "executor_elapsed_seconds": 0.0, "reduction_record": {
                    "status": "running", "active_stage": {"kind": "bkz", "beta": 30, "tour": 1},
                    "stages": [], "actual": {"completed_tours": 0, "completed_lll_calls": 0}}}

    def run_worker(self):
        return worker.run_attempt(self.request, clock=self.clock,
                                  events_path=self.directory / "events.jsonl",
                                  result_path=self.directory / "result.json")

    def test_default_executor_sink_is_durable_and_records_inherited_deadlines(self):
        def execute(rows, protocol, *, event_sink):
            payload = self.snapshot()
            event_sink(payload)
            payload["reduction_record"]["actual"]["completed_tours"] = 999
            return fake_reduction(rows, protocol)
        with patch.object(worker, "execute_reduction", side_effect=execute):
            result = self.run_worker()
        snapshot = result["reduction_progress"]
        self.assertEqual(snapshot["reduction_record"]["actual"]["completed_tours"], 0)
        self.assertEqual(snapshot["inherited_work_deadline"], 30)
        self.assertEqual(snapshot["inherited_hard_deadline"], 32)
        self.assertTrue(snapshot["observed_before_work_deadline"])
        events = [json.loads(line) for line in (self.directory / "events.jsonl").read_text().splitlines()]
        saved = next(event for event in events if event["event"] == "reduction_progress")
        self.assertEqual(saved["elapsed_seconds"], 10)
        self.assertEqual(saved["observed_monotonic"], 10)

    def test_interruption_keeps_started_tour_without_fabricated_completion(self):
        def execute(rows, protocol, *, event_sink):
            event_sink(self.snapshot())
            raise KeyboardInterrupt("test interruption, no real backend")
        with patch.object(worker, "execute_reduction", side_effect=execute):
            with self.assertRaises(KeyboardInterrupt):
                self.run_worker()
        saved = json.loads((self.directory / "result.json").read_text())
        snapshot = saved["reduction_progress"]
        self.assertEqual(snapshot["reduction_record"]["actual"]["completed_tours"], 0)
        self.assertEqual(snapshot["reduction_record"]["active_stage"]["tour"], 1)
        self.assertNotIn("reduction", saved)

    def test_late_observation_is_marked_late_without_extending_deadline(self):
        def execute(rows, protocol, *, event_sink):
            self.clock.advance(21)
            event_sink(self.snapshot())
            return fake_reduction(rows, protocol)
        with patch.object(worker, "execute_reduction", side_effect=execute):
            result = self.run_worker()
        self.assertFalse(result["reduction_progress"]["observed_before_work_deadline"])
        self.assertEqual(result["reduction_progress"]["inherited_work_deadline"], 30)
        self.assertEqual(result["status"], "work_deadline")


if __name__ == "__main__":
    unittest.main()
