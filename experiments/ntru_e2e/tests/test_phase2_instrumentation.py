"""Synthetic audits of instrumentation versions, censoring and kernel work."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from experiments.ntru_e2e.evaluate_phase2 import METHODS, evaluate_run, evaluate_trial


def fixture():
    public = {"schema": "ntru-e2e-public-v1", "instance_id": "synthetic", "n": 4,
              "q": 17, "ring": "x^n+1", "h": [0, 16, 0, 0],
              "distribution": {"kind": "fixed_weight_ternary", "w_f": 1, "w_g": 1}}
    truth = {"instance_id": "synthetic", "f": [0, 0, 0, 1], "g": [1, 0, 0, 0]}
    positive = {"f": truth["f"], "g": truth["g"], "v": [0] * 5,
                "coefficients": [0] * 5, "target_id": 0, "residual_sq": 1}
    negative = {"f": [0, 0, 0, -1], "g": [-1, 0, 0, 0], "v": [0] * 5,
                "coefficients": [0] * 5, "target_id": 1, "residual_sq": 1, "call_id": 1}
    identity = [[int(i == j) for j in range(5)] for i in range(5)]
    result = {"public_sha256": "public", "config_sha256": "config", "status": "complete",
              "coordinates": {"P": [1, 2], "C": [0], "T": [3]},
              "guesses": [[1], [-1]], "targets": [[-1, 0, 0, 0, 0], [1, 0, 0, 0, 0]],
              "bounds_sq": [1, 1], "bases": {name: deepcopy(identity) for name in ("lll", "bkz", "slicer")},
              "arms": {method: {"status": "complete", "seconds": 2, "first_valid_seconds": None,
                                "total_first_valid_seconds": None, "candidates": []} for method in METHODS},
              "events": [{"event": "slicer_call_started", "function": "bdgl_like_sieve", "call_id": 1,
                          "external_ids": [0, 1]},
                         {"event": "slicer_call_finished", "function": "bdgl_like_sieve", "call_id": 1},
                         {"event": "candidate_validated", "call_id": 1, "target_id": 1, "residual_sq": 1}]}
    result["arms"]["direct_tail"].update(candidates=[positive], first_valid_seconds=0.1, total_first_valid_seconds=0.2)
    result["arms"]["slicer"].update(candidates=[negative], first_valid_seconds=0.5, total_first_valid_seconds=10)
    return public, truth, result


class PhaseTwoInstrumentationTests(unittest.TestCase):
    def setUp(self):
        self.public, self.truth, self.result = fixture()

    def audit(self):
        return evaluate_trial(self.public, self.truth, self.result,
                              public_sha256="public", config_sha256="config")

    def test_baseline_and_slicer_censoring_are_reported_separately(self):
        self.result["arms"]["slicer"]["status"] = "running"
        comparison = self.audit()["comparison"]
        self.assertFalse(comparison["comparison_censored"])
        self.assertTrue(comparison["slicer_censored"])
        self.assertTrue(comparison["full_comparison_censored"])
        # A candidate already constructed can prove existence despite a later timeout.
        self.assertTrue(comparison["conclusive_extra_candidate"])
        self.assertFalse(comparison["time_comparison_available"])
        self.result["arms"]["slicer"]["status"] = "complete"
        self.result["arms"]["lll_babai"]["status"] = "running"
        comparison = self.audit()["comparison"]
        self.assertTrue(comparison["comparison_censored"])
        self.assertFalse(comparison["slicer_censored"])
        self.assertTrue(comparison["full_comparison_censored"])
        self.assertFalse(comparison["conclusive_extra_candidate"])

    def test_kernel_counts_separate_calls_unique_targets_and_resubmissions(self):
        self.result["events"] += [
            {"event": "target_insert_start", "call_id": 99, "external_ids": [0, 1]},
            {"event": "slicer_call_started", "function": "bdgl_like_sieve", "call_id": 2,
             "external_ids": [1]},
            {"event": "slicer_call_finished", "function": "bdgl_like_sieve", "call_id": 3},
            {"event": "babai_diagnostic_complete", "call_id": 4, "external_ids": [0]}]
        kernel = self.audit()["kernel"]
        self.assertEqual(kernel, {"started_calls": 2, "completed_calls": 1, "censored_calls": 1,
                                  "submitted_target_count": 2, "completed_target_count": 2,
                                  "submitted_target_call_count": 3, "completed_target_call_count": 2})

    def write_inventory(self, root):
        config = root / "config.json"
        config.write_text("{}\n")
        config_sha = hashlib.sha256(config.read_bytes()).hexdigest()
        instances = []
        for instance_id, case_id, versions in (
                ("A", "case_one", [None, "v2_compact"]),
                ("B", "case_one", [None, "v2_compact"]),
                ("C", "case_two", ["v2_compact"])):
            public, truth = deepcopy(self.public), deepcopy(self.truth)
            public["instance_id"] = truth["instance_id"] = instance_id
            public_path = root / f"{instance_id}.public.json"
            truth_path = root / f"{instance_id}.truth.json"
            public_path.write_text(json.dumps(public))
            public_sha = hashlib.sha256(public_path.read_bytes()).hexdigest()
            truth["public_sha256"] = public_sha
            truth_path.write_text(json.dumps(truth))
            instance = {"case_id": case_id, "public": public_path.name,
                        "truth": truth_path.name, "trials": []}
            for index, version in enumerate(versions):
                result = deepcopy(self.result)
                result.update(public_sha256=public_sha, config_sha256=config_sha,
                              instrumentation_version="untrusted_result_version")
                # Only A/legacy and C/v2 recover in direct_tail; only A/v2 in Slicer.
                if not ((instance_id == "A" and version is None) or instance_id == "C"):
                    result["arms"]["direct_tail"]["candidates"] = []
                if not (instance_id == "A" and version == "v2_compact"):
                    result["arms"]["slicer"]["candidates"] = []
                if instance_id == "B":
                    result["events"] = result["events"][:1]
                    result["arms"]["slicer"]["status"] = "running"
                result_path = root / f"{instance_id}.trial{index}.json"
                result_path.write_text(json.dumps(result))
                trial = {"result": result_path.name, "config": config.name,
                         "config_sha256": config_sha, "charged_online_seconds": 3,
                         "online_command": {"wall_seconds": 3, "status": "finished"}}
                if version is not None:
                    trial["instrumentation_version"] = version
                instance["trials"].append(trial)
            instances.append(instance)
        inventory = root / "online_inventory.json"
        inventory.write_text(json.dumps({"config": config.name, "config_sha256": config_sha,
                                         "instances": instances}))
        return inventory

    def test_versions_recompute_instance_success_and_coverage_from_their_own_trials(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_inventory(root)
            before = {path: path.read_bytes() for path in root.iterdir()}
            report = evaluate_run(root)
            self.assertEqual({path: path.read_bytes() for path in before}, before)
            versions = {group["instrumentation_version"]: group for group in report["instrumentation_versions"]}
            self.assertEqual(set(versions), {"v1/unknown", "v2_compact"})
            legacy, compact = versions["v1/unknown"], versions["v2_compact"]
            self.assertEqual((legacy["instance_count"], legacy["trial_count"]), (2, 2))
            self.assertEqual((compact["instance_count"], compact["trial_count"]), (3, 3))
            self.assertEqual(legacy["arms"]["direct_tail"]["recovered_instances"], 1)
            self.assertEqual(compact["arms"]["direct_tail"]["recovered_instances"], 1)
            self.assertEqual(legacy["arms"]["slicer"]["recovered_instances"], 0)
            self.assertEqual(compact["arms"]["slicer"]["recovered_instances"], 1)
            self.assertEqual(report["aggregate"]["arms"]["direct_tail"]["recovered_instances"], 2)
            cases = {case["case_id"]: case for case in report["cases"]}
            first_versions = {group["instrumentation_version"]: group
                              for group in cases["case_one"]["instrumentation_versions"]}
            self.assertEqual(first_versions["v2_compact"]["arms"]["direct_tail"]["recovered_instances"], 0)
            self.assertEqual(first_versions["v1/unknown"]["arms"]["direct_tail"]["recovered_instances"], 1)
            self.assertEqual(legacy["kernel"]["started_calls"], 2)
            self.assertEqual(legacy["kernel"]["completed_calls"], 1)
            self.assertEqual(compact["kernel"]["started_calls"], 3)
            self.assertEqual(compact["kernel"]["completed_calls"], 2)
            self.assertEqual(cases["case_one"]["kernel"]["submitted_target_count"], 8)
            self.assertEqual(cases["case_one"]["kernel"]["completed_target_count"], 4)
            self.assertEqual(report["aggregate"]["comparison"]["censored_trials"], 0)
            self.assertEqual(report["aggregate"]["comparison"]["slicer_censored_trials"], 2)
            self.assertEqual(report["aggregate"]["comparison"]["full_comparison_censored_trials"], 2)
            self.assertEqual(report["metrics"]["full_censored_trial_comparisons"], 2)
            self.assertEqual(report["instances"][0]["trials"][0]["instrumentation_version"], "v1/unknown")
            self.assertIn("not used for speed comparisons", report["timing_scope"])

    def test_invalid_inventory_version_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inventory = self.write_inventory(root)
            data = json.loads(inventory.read_text())
            data["instances"][0]["trials"][0]["instrumentation_version"] = {"version": "v2"}
            inventory.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "instrumentation_version"):
                evaluate_run(root)


if __name__ == "__main__":
    unittest.main()
