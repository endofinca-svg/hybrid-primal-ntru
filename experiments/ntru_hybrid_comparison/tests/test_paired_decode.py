"""Paired-worker boundary tests using explicitly simulated backend events.

The inputs below are hand-written algebra fixtures, not key generation or a
backend reduction.  Real Babai/Slicer acceptance is recorded separately.
"""

from copy import deepcopy
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from experiments.ntru_hybrid_comparison.candidates import CandidatePolicy
from experiments.ntru_hybrid_comparison.inputs import (
    attach_reduction, build_input_bundle, save_input_bundle,
)


def paired_fixture(*, representation="centered_mod_q", negative_h=False):
    """Eight external tails; four have the public weight-one distribution."""
    public = {
        "schema": "ntru-e2e-public-v1",
        "instance_id": "phase2c-hand-algebra-fixture-not-an-attack",
        "n": 4, "q": 17, "ring": "x^n+1",
        "h": [16 if negative_h else 1, 0, 0, 0],
        "distribution": {"kind": "fixed_weight_ternary", "w_f": 1, "w_g": 1},
    }
    bundle = build_input_bundle(
        public, [], [2, 3], [0, 1], CandidatePolicy("full_nonzero"),
        remaining_positions=[0, 1, 2, 3],
        target_representation=representation,
        provenance={"kind": "test_fixture"},
    )
    rows = bundle["basis"]["original"]
    dimension = len(rows)
    return attach_reduction(bundle, {
        "original_basis": deepcopy(rows), "reduced_basis": deepcopy(rows),
        "transform": [[int(i == j) for j in range(dimension)]
                      for i in range(dimension)],
        "protocol": deepcopy(bundle["basis"]["protocol"]),
        "record": {
            "status": "completed", "elapsed_seconds": 0.0,
            "cumulative_seconds": 0.0, "stages": [],
            "timing_source": "synthetic_test_fixture_not_a_backend_measurement",
        },
    })


def candidate_record(identifier, *, method="E", coefficients=False, vector=None):
    record = {
        "backend_integer_id": identifier,
        "output_origin": "babai_only" if method == "E" else "slicer_tail_with_babai_lift",
        "slicer_invoked": method == "D",
        "slicer_used_in_returned_candidate": method == "D",
    }
    record["coefficients" if coefficients else "v"] = [0] * 6 if vector is None else vector
    return record


class SimulatedBackendFactory:
    """Explicitly simulated responses, never an alternative decoder backend."""

    def __init__(self, script=None, *, coefficients=False, mutate_preprocess=False):
        self.script = script
        self.coefficients = coefficients
        self.mutate_preprocess = mutate_preprocess
        self.instances = []

    def __call__(self, working, method, config):
        owner = self

        class SimulatedBackend:
            def __init__(self):
                self.working, self.method, self.config = working, method, config
                self.calls, self.preprocess_calls = [], 0
                self.metrics = {"preprocess_count": 0, "slicer_kernel_calls": 0,
                                "babai_precheck_target_count": 0, "slicer_target_count": 0}
                self.metadata = {"simulation_only": True, "hidden_reduction_calls": 0,
                                 "basis_changed": False}
                owner.instances.append(self)

            def preprocess(self):
                self.preprocess_calls += 1
                if owner.mutate_preprocess:
                    self.working["rows"][0][0] += 1
                self.metrics["preprocess_count"] = int(method == "D")
                return {"preprocess_count": int(method == "D"),
                        "simulation_only": True}

            def decode(self, targets, bounds_sq, backend_integer_ids):
                self.calls.append((deepcopy(targets), list(bounds_sq), list(backend_integer_ids)))
                if owner.script is not None:
                    return owner.script(self, targets, bounds_sq, backend_integer_ids)
                return {
                    "candidates": [candidate_record(identifier, method=method,
                                                     coefficients=owner.coefficients)
                                   for identifier in backend_integer_ids],
                    "completed_ids": list(backend_integer_ids), "errored_ids": [],
                    "cancelled_ids": [], "projection_groups": [],
                    "timings": {"target_prepare_seconds": 0.0,
                                "decode_seconds": 0.0, "lift_seconds": 0.0},
                    "metrics": {"simulation_only": True},
                }

        return SimulatedBackend()


class PairedDecodeBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.path = self.directory / "hand-input.json"
        self.bundle = paired_fixture()
        save_input_bundle(self.path, self.bundle)

    def run_worker(self, factory=None, *, method="E", **kwargs):
        # The new worker is imported lazily so this hand fixture can also be
        # reused by independent tests without importing a backend.
        from experiments.ntru_hybrid_comparison.paired_decode import run_fixed_basis
        if factory is None:
            factory = SimulatedBackendFactory()
        return run_fixed_basis(self.path, method, backend_factory=factory, **kwargs)

    def assertConservation(self, progress):
        self.assertEqual(progress["submitted_count"], sum(progress[key] for key in (
            "completed_count", "errored_count", "cancelled_count", "pending_count")))
        self.assertEqual(progress["target_count"],
                         progress["submitted_count"] + progress["unsubmitted_count"])

    def test_coefficients_are_converted_to_ambient_vector_before_public_check(self):
        result = self.run_worker(SimulatedBackendFactory(coefficients=True))
        progress = result["progress"]
        self.assertEqual(progress["submitted_count"], 8)
        self.assertEqual(progress["completed_count"], 8)
        self.assertEqual(progress["returned_candidate_count"], 8)
        self.assertEqual(progress["public_valid_target_count"], 4)
        self.assertEqual(progress["public_valid_candidate_count"], 4)
        self.assertConservation(progress)
        self.assertEqual(sum(record["public_validation"]["public_valid"]
                             for record in result["candidates"]), 4)

    def test_full_batch_continues_after_valid_output_and_shares_preprocessing(self):
        factory = SimulatedBackendFactory()
        result = self.run_worker(factory, method="D", batch_size=2)
        self.assertEqual(result["progress"]["completed_count"], 8)
        self.assertEqual(len(factory.instances), 1)
        self.assertEqual(factory.instances[0].preprocess_calls, 1)
        self.assertEqual(len(factory.instances[0].calls), 4)
        self.assertTrue(all(record["slicer_invoked"] for record in result["candidates"]))
        self.assertTrue(all(record["pipeline_method"] == "D" for record in result["candidates"]))

    def test_first_public_valid_drains_actual_submitted_chunk_then_stops(self):
        factory = SimulatedBackendFactory()
        result = self.run_worker(factory, batch_size=2,
                                 mode="fixed_basis_first_public_valid")
        progress = result["progress"]
        self.assertEqual(progress["submitted_count"], 2)
        self.assertEqual(progress["completed_count"], 2)
        self.assertEqual(progress["unsubmitted_count"], 6)
        self.assertEqual(progress["errored_count"], 0)
        self.assertEqual(len(factory.instances[0].calls), 1)
        self.assertIsNotNone(result["first_public_valid"])
        first = result["first_public_valid"]["progress"]
        self.assertEqual(first["submitted_count"], 2)
        self.assertEqual(first["completed_count"], 2)
        self.assertEqual(first["pending_count"], 0)
        self.assertEqual(first["public_valid_target_count"], 1)
        self.assertEqual(result["after_first_public_valid"]["returned_candidates_ingested"], 1)
        self.assertConservation(progress)

    def test_false_backend_success_labels_do_not_trigger_early_stop(self):
        def script(backend, targets, bounds, ids):
            records = []
            for identifier in ids:
                record = candidate_record(identifier, vector=[17, 0, 0, 0, 0, 0])
                record.update(public_valid=True, solver_success=True, recovered=True)
                records.append(record)
            return {"candidates": records, "completed_ids": list(ids)}
        factory = SimulatedBackendFactory(script)
        result = self.run_worker(factory, batch_size=2,
                                 mode="fixed_basis_first_public_valid")
        self.assertEqual(result["progress"]["public_valid_target_count"], 0)
        self.assertEqual(result["progress"]["submitted_count"], 8)
        self.assertEqual(result["progress"]["completed_count"], 8)
        self.assertEqual(len(factory.instances[0].calls), 4)

    def test_explicit_no_candidate_is_normal_completion(self):
        factory = SimulatedBackendFactory(lambda backend, targets, bounds, ids: {
            "candidates": [], "completed_ids": list(ids)})
        result = self.run_worker(factory)
        progress = result["progress"]
        self.assertEqual(progress["completed_count"], 8)
        self.assertEqual(progress["returned_candidate_count"], 0)
        self.assertEqual(progress["errored_count"], 0)
        self.assertConservation(progress)

    def test_errors_cancellation_and_missing_results_are_distinct(self):
        factory = SimulatedBackendFactory(lambda backend, targets, bounds, ids: {
            "candidates": [candidate_record(ids[0])], "completed_ids": [ids[0]],
            "errored_ids": [ids[1]], "cancelled_ids": [ids[2]]})
        progress = self.run_worker(factory)["progress"]
        self.assertEqual(progress["completed_count"], 1)
        self.assertEqual(progress["errored_count"], 1)
        self.assertEqual(progress["cancelled_count"], 1)
        self.assertEqual(progress["pending_count"], 5)
        self.assertConservation(progress)

    def test_multi_candidate_target_completes_once(self):
        def script(backend, targets, bounds, ids):
            return {"candidates": [candidate_record(ids[0]), candidate_record(ids[0])],
                    "completed_ids": [ids[0]]}
        progress = self.run_worker(SimulatedBackendFactory(script))["progress"]
        self.assertEqual(progress["returned_candidate_count"], 2)
        self.assertEqual(progress["completed_count"], 1)
        self.assertEqual(progress["public_valid_candidate_count"], 2)
        self.assertEqual(progress["public_valid_target_count"], 1)
        self.assertEqual(progress["unique_public_valid_solution_count"], 1)
        self.assertConservation(progress)

    def test_unordered_output_uses_explicit_ids_for_tail_reconstruction(self):
        factory = SimulatedBackendFactory(lambda backend, targets, bounds, ids: {
            "candidates": [candidate_record(identifier) for identifier in reversed(ids)],
            "completed_ids": list(reversed(ids))})
        result = self.run_worker(factory)
        by_id = {row["backend_integer_id"]: row for row in self.bundle["id_mapping"]}
        for record in result["candidates"]:
            identity = by_id[record["backend_integer_id"]]
            self.assertEqual(record["candidate_sha_id"], identity["candidate_sha_id"])
            self.assertEqual(record["reconstructed"]["f"][:2], identity["u"])
        self.assertEqual(result["progress"]["public_valid_target_count"], 4)

    def test_projection_group_does_not_complete_missing_external_members(self):
        def script(backend, targets, bounds, ids):
            return {
                "candidates": [candidate_record(ids[0], method="D")],
                "completed_ids": [ids[0]],
                "projection_groups": [{"group_id": 0, "external_ids": list(ids)}],
            }
        progress = self.run_worker(SimulatedBackendFactory(script), method="D")["progress"]
        self.assertEqual(progress["submitted_count"], 8)
        self.assertEqual(progress["completed_count"], 1)
        self.assertEqual(progress["pending_count"], 7)
        self.assertEqual(progress["public_valid_target_count"], 1)

    def test_same_projection_members_use_their_own_tail_and_target(self):
        def script(backend, targets, bounds, ids):
            chosen = [ids[2], ids[0]]
            return {"candidates": [candidate_record(identifier, method="D")
                                   for identifier in chosen],
                    "completed_ids": chosen,
                    "projection_groups": [{"group_id": 0, "external_ids": chosen}]}
        result = self.run_worker(SimulatedBackendFactory(script), method="D")
        reconstructed = [record["reconstructed"]["f"] for record in result["candidates"]]
        self.assertEqual(reconstructed, [[0, -1, 0, 0], [-1, 0, 0, 0]])
        self.assertEqual(result["progress"]["public_valid_target_count"], 2)

    def test_unknown_id_cannot_attach_candidate_to_a_known_tail(self):
        factory = SimulatedBackendFactory(lambda backend, targets, bounds, ids: {
            "candidates": [candidate_record(987654321)],
            "completed_ids": list(ids) + [987654321]})
        result = self.run_worker(factory)
        self.assertEqual(result["progress"]["public_valid_target_count"], 0)
        self.assertEqual(result["progress"]["completed_count"], 8)
        self.assertTrue(result["protocol_errors"])
        self.assertConservation(result["progress"])

    def test_duplicate_terminal_is_diagnosed_and_never_counted_twice(self):
        factory = SimulatedBackendFactory(lambda backend, targets, bounds, ids: {
            "candidates": [], "completed_ids": list(ids) + [ids[0]]})
        result = self.run_worker(factory)
        self.assertEqual(result["progress"]["completed_count"], 7)
        self.assertEqual(result["progress"]["errored_count"], 1)
        self.assertTrue(result["protocol_errors"])
        self.assertConservation(result["progress"])

    def test_conflicting_terminal_events_error_one_target_without_double_counting(self):
        factory = SimulatedBackendFactory(lambda backend, targets, bounds, ids: {
            "candidates": [], "completed_ids": list(ids),
            "cancelled_ids": [ids[0]]})
        result = self.run_worker(factory)
        progress = result["progress"]
        self.assertEqual(progress["submitted_count"], 8)
        self.assertEqual(progress["completed_count"], 7)
        self.assertEqual(progress["errored_count"], 1)
        self.assertEqual(progress["cancelled_count"], 0)
        self.assertEqual(progress["pending_count"], 0)
        self.assertTrue(result["protocol_errors"])
        self.assertConservation(progress)

    def test_raw_and_centered_representatives_reconstruct_identically(self):
        reconstructed = []
        for representation in ("raw", "centered_mod_q"):
            bundle = paired_fixture(representation=representation, negative_h=True)
            save_input_bundle(self.path, bundle)
            by_id = {row["backend_integer_id"]: row for row in bundle["id_mapping"]}
            def script(backend, targets, bounds, ids):
                records = []
                for identifier in ids:
                    u = by_id[identifier]["u"]
                    vector = [-17 * value for value in u] + [0] * 4 if representation == "raw" else [0] * 6
                    records.append(candidate_record(identifier, vector=vector))
                return {"candidates": records, "completed_ids": list(ids)}
            result = self.run_worker(SimulatedBackendFactory(script))
            self.assertEqual(result["progress"]["public_valid_target_count"], 4)
            reconstructed.append([record["reconstructed"] for record in result["candidates"]])
        self.assertEqual(*reconstructed)

    def test_paired_runs_start_from_identical_inputs_with_private_copies(self):
        factory_e, factory_d = SimulatedBackendFactory(), SimulatedBackendFactory()
        result_e = self.run_worker(factory_e, method="E")
        result_d = self.run_worker(factory_d, method="D")
        self.assertEqual(result_e["pairing"], result_d["pairing"])
        self.assertEqual(result_e["pairing"]["starting_basis_sha256"],
                         self.bundle["basis"]["reduced_sha256"])
        self.assertEqual(result_e["pairing"]["candidate_ordered_sha256"],
                         self.bundle["candidate_batch"]["ordered_sha256"])
        self.assertEqual(result_e["pairing"]["ordered_targets_sha256"],
                         self.bundle["ordered_targets_sha256"])
        working_e, working_d = factory_e.instances[0].working, factory_d.instances[0].working
        self.assertEqual(working_e["rows"], working_d["rows"])
        self.assertEqual(working_e["targets"], working_d["targets"])
        working_d["rows"][0][0] += 1
        working_d["targets"][0][0] += 1
        working_d["id_mapping"][0]["u"][0] = 99
        self.assertEqual(working_e["rows"], self.bundle["basis"]["reduced"])
        self.assertEqual(working_e["targets"], [row["used"] for row in self.bundle["targets"]])
        self.assertEqual(working_e["id_mapping"], self.bundle["id_mapping"])

    def test_worker_never_calls_reference_classification(self):
        with patch("experiments.ntru_hybrid_comparison.offline.classify_against_reference",
                   side_effect=AssertionError("reference classification in online worker")), \
             patch("experiments.ntru_hybrid_comparison.offline.diagnose_coverage",
                   side_effect=AssertionError("reference coverage in online worker")):
            self.assertEqual(self.run_worker()["progress"]["public_valid_target_count"], 4)

    def test_malformed_or_nonlattice_vectors_cannot_pass_public_validation(self):
        for vector in ([0] * 5, [False] + [0] * 5, [0.0] + [0] * 5,
                       [1, 0, 0, 0, 0, 0]):
            with self.subTest(vector=vector):
                def script(backend, targets, bounds, ids):
                    return {"candidates": [candidate_record(ids[0], vector=vector)],
                            "completed_ids": list(ids)}
                result = self.run_worker(SimulatedBackendFactory(script))
                self.assertEqual(result["progress"]["public_valid_target_count"], 0)
                self.assertTrue(result["candidates"][0]["conversion_rejection"])
                self.assertIsNone(result["candidates"][0]["reconstructed"])
                self.assertEqual(result["progress"]["completed_count"], 7)
                self.assertEqual(result["progress"]["errored_count"], 1)
                self.assertTrue(result["protocol_errors"])
                self.assertConservation(result["progress"])

    def test_decode_exception_does_not_fabricate_normal_completion(self):
        def script(backend, targets, bounds, ids):
            raise RuntimeError("explicitly simulated backend failure")
        result = self.run_worker(SimulatedBackendFactory(script))
        progress = result["progress"]
        self.assertEqual(progress["submitted_count"], 8)
        self.assertEqual(progress["completed_count"], 0)
        self.assertEqual(progress["returned_candidate_count"], 0)
        self.assertEqual(progress["errored_count"] + progress["pending_count"], 8)
        self.assertConservation(progress)

    def test_method_name_d_does_not_relabel_babai_only_output(self):
        def script(backend, targets, bounds, ids):
            return {"candidates": [candidate_record(ids[0], method="E")],
                    "completed_ids": list(ids)}
        result = self.run_worker(SimulatedBackendFactory(script), method="D")
        record = result["candidates"][0]
        self.assertEqual(record["pipeline_method"], "D")
        self.assertEqual(record["output_origin"], "babai_only")
        self.assertFalse(record["slicer_invoked"])
        self.assertFalse(record["slicer_used_in_returned_candidate"])

    def test_original_only_input_is_rejected_before_backend_construction(self):
        bundle = build_input_bundle(
            self.bundle["public_instance"], [], [2, 3], [0, 1], CandidatePolicy("full_nonzero"),
            remaining_positions=[0, 1, 2, 3], provenance={"kind": "test_fixture"})
        save_input_bundle(self.path, bundle)
        factory = SimulatedBackendFactory()
        with self.assertRaises(ValueError):
            self.run_worker(factory)
        self.assertEqual(factory.instances, [])

    def test_hidden_basis_change_is_detected_before_decoding(self):
        factory = SimulatedBackendFactory(mutate_preprocess=True)
        result = self.run_worker(factory, method="D")
        self.assertEqual(result["progress"]["completed_count"], 0)
        self.assertEqual(len(factory.instances[0].calls), 0)
        self.assertTrue(result["stop_reason"] in ("setup_error", "backend_error")
                        or any("chang" in str(error).lower()
                               for error in result["protocol_errors"]))

    def test_reported_hidden_reduction_is_rejected_even_if_basis_is_unchanged(self):
        delegate = SimulatedBackendFactory()
        def factory(working, method, config):
            backend = delegate(working, method, config)
            def preprocess():
                backend.metadata["hidden_reduction_calls"] = 1
                return {"preprocess_count": 1}
            backend.preprocess = preprocess
            return backend
        result = self.run_worker(factory, method="D")
        self.assertEqual(result["progress"]["submitted_count"], 0)
        self.assertEqual(result["stop_reason"], "setup_error")
        self.assertEqual(delegate.instances[0].calls, [])

    def test_ambient_vector_must_match_explicit_basis_coefficients(self):
        def script(backend, targets, bounds, ids):
            record = candidate_record(ids[0])
            record["coefficients"] = [1, 0, 0, 0, 0, 0]
            return {"candidates": [record], "completed_ids": list(ids)}
        result = self.run_worker(SimulatedBackendFactory(script))
        self.assertEqual(result["progress"]["public_valid_candidate_count"], 0)
        self.assertIn("differs", result["candidates"][0]["conversion_rejection"])

    def test_exclusive_phases_sum_to_wall_without_adding_nested_kernel_time(self):
        def script(backend, targets, bounds, ids):
            started = time.monotonic()
            time.sleep(0.003)
            measured = time.monotonic() - started
            return {
                "candidates": [candidate_record(identifier, method="D") for identifier in ids],
                "completed_ids": list(ids),
                "timings": {"target_prepare_seconds": 0.0, "decode_seconds": measured,
                            "lift_seconds": 0.0,
                            "slicer_kernel_seconds_nested_in_decode": measured},
            }
        result = self.run_worker(SimulatedBackendFactory(script), method="D")
        timing = result["timings"]
        exclusive = ("input_load_verify_seconds", "gso_initialization_seconds",
                     "extra_preprocess_seconds", "target_prepare_seconds", "decode_seconds",
                     "lift_reconstruct_seconds", "public_verify_seconds", "result_output_seconds",
                     "bookkeeping_seconds")
        for name in exclusive + ("total_wall_seconds",):
            self.assertGreaterEqual(timing[name], 0.0, name)
        self.assertAlmostEqual(sum(timing[name] for name in exclusive),
                               timing["total_wall_seconds"], places=6)
        self.assertGreater(timing["decode_seconds"], 0.0)
        self.assertTrue(result["backend_batches"])
        self.assertEqual(result["backend_batches"][0]["backend_timings"][
            "slicer_kernel_seconds_nested_in_decode"], timing["decode_seconds"])

    def test_mock_worker_runs_with_reference_and_reduction_backend_imports_blocked(self):
        # This proves online plumbing needs no reference import or solver call.
        # It is deliberately separate from the recorded true backend checks.
        probe = r'''
import importlib.abc
import sys
from experiments.ntru_hybrid_comparison.tests.test_paired_decode import SimulatedBackendFactory
blocked = ("experiments.ntru_hybrid_comparison.offline",
           "experiments.ntru_e2e.generate", "fpylll", "g6k")
class RejectHiddenDependency(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + ".") for name in blocked):
            raise AssertionError("forbidden online dependency: " + fullname)
sys.meta_path.insert(0, RejectHiddenDependency())
from experiments.ntru_hybrid_comparison.paired_decode import run_fixed_basis
result = run_fixed_basis(sys.argv[1], "E", backend_factory=SimulatedBackendFactory())
assert result["progress"]["public_valid_target_count"] == 4
assert not any(name == prefix or name.startswith(prefix + ".")
               for name in sys.modules for prefix in blocked)
'''
        result = subprocess.run([sys.executable, "-B", "-c", probe, str(self.path)],
                                cwd=Path(__file__).resolve().parents[3],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


