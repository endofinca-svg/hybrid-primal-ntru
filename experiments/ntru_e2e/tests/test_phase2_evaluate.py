"""Small exact counterexamples for the independent phase-two audit."""
from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiments.ntru_e2e import evaluate_phase2 as evaluator
from experiments.ntru_e2e.evaluate_phase2 import (
    METHODS, canonical_class, evaluate_run, evaluate_trial, main,
)


class PhaseTwoEvaluationTests(unittest.TestCase):
    def setUp(self):
        # x^3 * (-x) = 1 in Z_17[x]/(x^4+1).  The two tail guesses
        # independently reconstruct a secret and its global negative.
        self.public = {"schema": "ntru-e2e-public-v1", "instance_id": "test",
                       "n": 4, "q": 17, "ring": "x^n+1", "h": [0, 16, 0, 0],
                       "distribution": {"kind": "fixed_weight_ternary", "w_f": 1, "w_g": 1}}
        self.truth = {"instance_id": "test", "f": [0, 0, 0, 1], "g": [1, 0, 0, 0]}
        positive = {"f": self.truth["f"], "g": self.truth["g"], "v": [0] * 5,
                    "coefficients": [0] * 5, "target_id": 0, "residual_sq": 1}
        negative = {"f": [0, 0, 0, -1], "g": [-1, 0, 0, 0], "v": [0] * 5,
                    "coefficients": [0] * 5, "target_id": 1, "residual_sq": 1,
                    "call_id": 1}
        identity = [[int(i == j) for j in range(5)] for i in range(5)]
        self.result = {"public_sha256": "public", "config_sha256": "config", "status": "complete",
                       "coordinates": {"P": [1, 2], "C": [0], "T": [3]},
                       "guesses": [[1], [-1]], "targets": [[-1, 0, 0, 0, 0], [1, 0, 0, 0, 0]],
                       "bounds_sq": [1, 1], "bases": {name: deepcopy(identity) for name in ("lll", "bkz", "slicer")},
                       "arms": {method: {"status": "complete", "seconds": 2,
                                         "first_valid_seconds": None, "candidates": []} for method in METHODS},
                       "events": [{"event": "slicer_call_started", "function": "bdgl_like_sieve", "call_id": 1,
                                   "external_ids": [0, 1]},
                                  {"event": "slicer_call_finished", "function": "bdgl_like_sieve", "call_id": 1},
                                  {"event": "candidate_validated", "call_id": 1, "target_id": 1, "residual_sq": 1}]}
        self.result["arms"]["direct_tail"].update(candidates=[positive], first_valid_seconds=0.1)
        self.result["arms"]["slicer"].update(candidates=[negative], first_valid_seconds=0.5)

    def audit(self, result=None):
        return evaluate_trial(self.public, self.truth, result or self.result,
                              public_sha256="public", config_sha256="config")

    def test_raw_novelty_does_not_imply_new_rotation_class(self):
        report = self.audit()
        self.assertTrue(report["integrity_verified"])
        self.assertEqual(report["arms"]["slicer"]["verified_candidate_count"], 1)
        comparison = report["comparison"]
        self.assertEqual(comparison["observed_slicer_only_raw_candidate_count"], 1)
        self.assertEqual(comparison["observed_slicer_new_class_count"], 0)
        self.assertTrue(comparison["conclusive_extra_candidate"])
        self.assertFalse(comparison["slicer_first_valid_faster_than_baseline_union"])
        self.assertEqual(report["coverage"]["compatible_nonzero_tail_rotation_count"], 2)
        self.assertEqual(report["coverage"]["completed_compatible_nonzero_tail_rotation_count"], 2)

    def test_union_includes_every_baseline_and_incomplete_is_censored(self):
        candidate = deepcopy(self.result["arms"]["slicer"]["candidates"][0])
        self.result["arms"]["bkz_babai"].update(candidates=[candidate], first_valid_seconds=0.2)
        comparison = self.audit()["comparison"]
        self.assertEqual(comparison["observed_slicer_only_raw_candidate_count"], 0)
        self.result["arms"]["bkz_babai"]["candidates"] = []
        self.result["arms"]["lll_babai"]["status"] = "running"
        comparison = self.audit()["comparison"]
        self.assertTrue(comparison["comparison_censored"])
        self.assertEqual(comparison["observed_slicer_only_raw_candidate_count"], 1)
        self.assertFalse(comparison["conclusive_extra_candidate"])
        self.assertFalse(comparison["time_comparison_available"])

    def test_slicer_coefficients_must_reconstruct_the_recorded_vector(self):
        self.result["arms"]["slicer"]["candidates"][0]["coefficients"][0] = 1
        check = self.audit()["arms"]["slicer"]["candidate_checks"][0]
        self.assertEqual(check["reason"], "coefficients_times_basis_mismatch")

    def test_trace_requires_kernel_completion_and_matching_lifting_event(self):
        for mutation in ("missing_finish", "wrong_target", "early_candidate", "wrong_radius"):
            result = deepcopy(self.result)
            if mutation == "missing_finish":
                del result["events"][1]
            elif mutation == "wrong_target":
                result["events"][0]["external_ids"] = [0]
            elif mutation == "early_candidate":
                result["events"][1], result["events"][2] = result["events"][2], result["events"][1]
            else:
                result["events"][2]["residual_sq"] = 2
            with self.subTest(mutation=mutation):
                self.assertEqual(self.audit(result)["arms"]["slicer"]["verified_candidate_count"], 0)

    def test_signed_rows_are_checked_with_zero_tail_and_exact_coefficients(self):
        candidate = {"f": [1, 0, 0, 0], "g": [0, -1, 0, 0],
                     "v": [0, -1, 0, 0, 1], "coefficients": [1, 0, 0, 0, 0],
                     "target_id": None, "residual_sq": 2, "row_index": 0, "sign": 1}
        self.result["bases"]["lll"][0] = candidate["v"]
        self.result["arms"]["lll_rows"]["candidates"] = [candidate]
        check = self.audit()["arms"]["lll_rows"]["candidate_checks"][0]
        self.assertTrue(check["valid"])
        self.assertIsNotNone(check["equivalence"])
        self.assertEqual(canonical_class(candidate), canonical_class(self.truth))
        candidate["sign"] = -1
        self.assertEqual(self.audit()["arms"]["lll_rows"]["candidate_checks"][0]["reason"],
                         "row_coefficients_not_signed_unit_vector")

    def test_target_hash_and_exact_bound_are_audited(self):
        for key in ("hash", "target", "bound", "residual", "partition"):
            result = deepcopy(self.result)
            if key == "hash":
                result["public_sha256"] = "wrong"
            elif key == "target":
                result["targets"][0][0] += 17
            elif key == "bound":
                result["bounds_sq"][0] = 2
            elif key == "residual":
                result["arms"]["slicer"]["candidates"][0]["residual_sq"] = 0
            else:
                result["coordinates"]["P"] = [0, 1]
            with self.subTest(key=key):
                report = self.audit(result)
                self.assertEqual(report["arms"]["slicer"]["verified_candidate_count"], 0)
        self.result["config_sha256"] = "wrong"
        self.assertIn("config_sha256_mismatch", self.audit()["integrity_errors"])

    def test_coverage_counts_kernel_submission_and_completion_separately(self):
        self.result["events"] = [
            {"event": "target_insert_start", "call_id": 8, "external_ids": [0, 1]},
            {"event": "slicer_call_started", "function": "bdgl_like_sieve", "call_id": 9,
             "external_ids": [0]}]
        coverage = self.audit()["coverage"]
        self.assertEqual(coverage["submitted_target_ids"], [0])
        self.assertEqual(coverage["submitted_compatible_nonzero_tail_rotation_count"], 1)
        self.assertEqual(coverage["completed_compatible_nonzero_tail_rotation_count"], 0)

    def test_inventory_cli_preserves_online_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            public = directory / "public.json"
            truth = directory / "truth.json"
            result = directory / "result.json"
            config = directory / "config_phase2.json"
            config.write_text("{}\n")
            public.write_text(json.dumps(self.public))
            self.truth["public_sha256"] = hashlib.sha256(public.read_bytes()).hexdigest()
            truth.write_text(json.dumps(self.truth))
            self.result["public_sha256"] = self.truth["public_sha256"]
            self.result["config_sha256"] = hashlib.sha256(config.read_bytes()).hexdigest()
            result.write_text(json.dumps(self.result))
            summary = directory / "summary.json"
            summary.write_text(json.dumps({"instances": [{"case_id": "small", "instance_id": "test",
                                "public": str(public), "truth": str(truth), "trials": [{"result": str(result)}]}]}))
            before = {path: path.read_bytes() for path in (summary, config, public, truth, result)}
            report = evaluate_run(directory)
            self.assertEqual(report["metrics"]["verified_candidates_by_arm"]["slicer"], 1)
            self.assertTrue(report["config_hash_verified"])
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--run-dir", str(directory)]), 0)
            self.assertTrue((directory / "evaluation_phase2.json").exists())
            self.assertEqual({path: path.read_bytes() for path in before}, before)

    def write_inventory(self, directory):
        """Use different plan and online configs so their hashes cannot alias."""
        paths = {name: directory / filename for name, filename in (
            ("public", "public.json"), ("truth", "truth.json"),
            ("result", "result.json"), ("plan", "config_phase2.json"),
            ("online", "online_config.json"), ("inventory", "online_inventory.json"))}
        paths["plan"].write_text('{"kind": "plan"}\n')
        paths["online"].write_text('{"kind": "online"}\n')
        paths["public"].write_text(json.dumps(self.public))
        truth, result = deepcopy(self.truth), deepcopy(self.result)
        truth["public_sha256"] = hashlib.sha256(paths["public"].read_bytes()).hexdigest()
        result.update(public_sha256=truth["public_sha256"],
                      config_sha256=hashlib.sha256(paths["online"].read_bytes()).hexdigest())
        paths["truth"].write_text(json.dumps(truth))
        paths["result"].write_text(json.dumps(result))
        inventory = {"config": paths["plan"].name,
                     "config_sha256": hashlib.sha256(paths["plan"].read_bytes()).hexdigest(),
                     "instances": [{"case_id": "small", "public": paths["public"].name,
                                    "truth": paths["truth"].name, "trials": [{
                                        "config": paths["online"].name,
                                        "config_sha256": result["config_sha256"],
                                        "result": paths["result"].name,
                                        "online_command": {"wall_seconds": 4.5, "status": "finished"},
                                        "charged_online_seconds": 5}]}]}
        paths["inventory"].write_text(json.dumps(inventory))
        return paths, inventory

    def test_trial_config_and_stable_inventory_have_independent_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            paths, inventory = self.write_inventory(directory)
            # A later lifecycle summary is intentionally not the online inventory.
            (directory / "summary.json").write_text('{"status": "finished"}')
            report = evaluate_run(directory)
            self.assertEqual(report["inventory_path"], str(paths["inventory"]))
            self.assertEqual(report["inventory_sha256"], hashlib.sha256(paths["inventory"].read_bytes()).hexdigest())
            trial = report["instances"][0]["trials"][0]
            self.assertTrue(trial["integrity_verified"])
            self.assertTrue(trial["config_hash_verified"])
            self.assertNotEqual(trial["config_sha256"], report["config_sha256"])
            case = report["cases"][0]
            self.assertEqual(case["arms"]["slicer"]["recovered_instances"], 1)
            self.assertEqual(case["arms"]["slicer"]["completed_trials"], 1)
            self.assertEqual(case["comparison"]["summed_trial_slicer_only_raw_candidates"], 1)
            self.assertEqual(case["comparison"]["summed_trial_slicer_new_classes"], 0)
            self.assertEqual(case["online_wall_seconds"]["sum"], 4.5)
            self.assertEqual(case["charged_online_seconds"]["sum"], 5)
            self.assertEqual(case["coverage"]["summed_trial_completed_target_count"], 2)
            paths["online"].write_text('{"kind": "tampered"}\n')
            with self.assertRaisesRegex(ValueError, "trial config SHA"):
                evaluate_run(directory)

    def test_recorded_result_must_match_its_online_config_not_the_plan(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            paths, inventory = self.write_inventory(directory)
            result = json.loads(paths["result"].read_text())
            result["config_sha256"] = inventory["config_sha256"]
            paths["result"].write_text(json.dumps(result))
            trial = evaluate_run(directory)["instances"][0]["trials"][0]
            self.assertIn("config_sha256_mismatch", trial["integrity_errors"])
            self.assertEqual(trial["arms"]["slicer"]["verified_candidate_count"], 0)

    def test_cumulative_worker_time_is_not_used_for_speed_comparison(self):
        self.result["arms"]["direct_tail"]["total_first_valid_seconds"] = 100
        self.result["arms"]["slicer"]["total_first_valid_seconds"] = 1
        comparison = self.audit()["comparison"]
        self.assertTrue(comparison["time_comparison_available"])
        self.assertFalse(comparison["slicer_first_valid_faster_than_baseline_union"])

    def test_case_selection_skips_unselected_files_and_reports_exact_scope(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            paths, inventory = self.write_inventory(directory)
            inventory["instances"].append({"case_id": "n128_excluded", "public": "missing_public.json",
                                            "truth": "missing_truth.json", "trials": [
                                                {"status": "pending", "result": "missing_result.json"}]})
            paths["inventory"].write_text(json.dumps(inventory))
            before = {path: path.read_bytes() for path in paths.values()}
            progress = []
            report = evaluate_run(directory, case_ids=["small", "small"], progress=progress.append)
            self.assertEqual(report["selected_case_ids"], ["small"])
            self.assertEqual(report["selection"], {
                "requested_case_ids": ["small"], "excluded_case_ids": ["n128_excluded"],
                "source_instance_count": 2, "source_trial_count": 2,
                "selected_instance_count": 1, "selected_trial_count": 1})
            self.assertEqual(report["metrics"]["trial_count"], 1)
            self.assertEqual(report["inventory_sha256"], hashlib.sha256(paths["inventory"].read_bytes()).hexdigest())
            self.assertEqual([event["event"] for event in progress], ["trial_started", "trial_finished"])
            self.assertTrue(progress[-1]["integrity_verified"])
            self.assertEqual(progress[-1]["trial_count"], 1)
            self.assertEqual({path: path.read_bytes() for path in before}, before)

    def test_case_selection_rejects_unknown_empty_and_unattempted_before_reading_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            paths, inventory = self.write_inventory(directory)
            paths["public"].unlink()
            for cases in (["unknown"], ["small", "unknown"], [], "small"):
                with self.subTest(cases=cases), self.assertRaises(ValueError):
                    evaluate_run(directory, case_ids=cases)
            for status in ("pending", "running", "not_run_budget", "not_run_scope"):
                inventory["instances"][0]["trials"][0]["status"] = status
                paths["inventory"].write_text(json.dumps(inventory))
                with self.subTest(status=status), self.assertRaisesRegex(ValueError, "has not been attempted"):
                    evaluate_run(directory, case_ids=["small"])

    def test_selected_cli_writes_separate_report_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            paths, inventory = self.write_inventory(directory)
            original_report = directory / "evaluation_phase2.json"
            original_report.write_text("preserved original report\n")
            output = directory / "selected_evaluation.json"
            argv = ["--run-dir", str(directory), "--case", "small", "--output", str(output)]
            with redirect_stdout(io.StringIO()) as stream:
                self.assertEqual(main(argv), 0)
            self.assertIn('"event": "trial_started"', stream.getvalue())
            self.assertEqual(json.loads(output.read_text())["selected_case_ids"], ["small"])
            self.assertEqual(original_report.read_text(), "preserved original report\n")
            before = output.read_bytes()
            with self.assertRaisesRegex(FileExistsError, "Preserving prior evaluation"):
                main(argv)
            self.assertEqual(output.read_bytes(), before)

    def test_exact_pair_cache_keeps_occurrence_checks_and_rejects_boolean_aliases(self):
        # This candidate shares f/g with a valid direct-tail candidate, but its
        # basis coefficients fail independently.  The cache cannot certify it.
        invalid_coefficients = deepcopy(self.result["arms"]["direct_tail"]["candidates"][0])
        invalid_coefficients["coefficients"][0] = 1
        self.result["arms"]["lll_babai"]["candidates"] = [invalid_coefficients]
        boolean_candidate = deepcopy(self.result["arms"]["direct_tail"]["candidates"][0])
        boolean_candidate["f"][0] = False
        self.result["arms"]["direct_tail"]["candidates"].append(boolean_candidate)
        with patch.object(evaluator, "validate_candidate", wraps=evaluator.validate_candidate) as validation, \
             patch.object(evaluator, "canonical_class", wraps=evaluator.canonical_class) as canonical, \
             patch.object(evaluator, "_equivalence", wraps=evaluator._equivalence) as equivalence:
            report = self.audit()
        self.assertEqual(validation.call_count, 3)
        self.assertEqual(canonical.call_count, 2)
        self.assertEqual(equivalence.call_count, 2)
        self.assertEqual(report["arms"]["lll_babai"]["candidate_checks"][0]["reason"],
                         "coefficients_times_basis_mismatch")
        self.assertEqual(report["arms"]["direct_tail"]["candidate_checks"][1]["reason"], "f_not_ternary")

    def test_candidate_caches_do_not_cross_evaluation_calls(self):
        first = self.audit()
        self.public["h"] = [0, 0, 0, 0]
        second = self.audit()
        self.assertEqual(first["arms"]["direct_tail"]["verified_candidate_count"], 1)
        self.assertEqual(second["arms"]["direct_tail"]["verified_candidate_count"], 0)


if __name__ == "__main__":
    unittest.main()
