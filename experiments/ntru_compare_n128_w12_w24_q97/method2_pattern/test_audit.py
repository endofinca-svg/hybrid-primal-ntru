"""Small exact examples for the pattern lattice's independent audit."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from .audit import audit_trial, initial_basis, main, orbit_coverage


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PatternAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.trial = self.root / "method2/trial"
        self.paths = {"public": self.root / "public.json", "truth": self.root / "truth.json",
                      "config": self.trial / "config.json", "result": self.trial / "result.json"}
        self.n = 16
        self.public = {"schema": "ntru-e2e-public-v1", "instance_id": "pattern-toy",
                       "n": 16, "q": 17, "ring": "x^n+1", "h": [1, 1] + [0] * 14,
                       "distribution": {"kind": "fixed_weight_ternary", "w_f": 1, "w_g": 2}}
        self.truth = {"instance_id": "pattern-toy", "f": [1] + [0] * 15,
                      "g": [1, 1] + [0] * 14}
        self.config = {"n": 16, "q": 17, "w_f": 1, "w_g": 2, "k": 1, "kappa": 0,
                       "bkz_start": 30, "bkz_stop": 31, "bkz_jump": 1,
                       "bkz_max_tours_per_beta": 2, "bkz_clean_stops_current_beta": False}
        self.pattern = [15]
        self.complement = list(range(15))
        self.initial = initial_basis(self.public, self.pattern)
        d = len(self.initial)
        order = [16] + list(range(16)) + list(range(17, d))
        self.rows = [list(self.initial[i]) for i in order]
        self.transform = [[int(i == j) for j in range(d)] for i in order]
        self.result = {
            "status": "success", "dimension": d,
            "coordinates": {"P": self.pattern, "C": self.complement, "T": []},
            "initial_basis_path": "initial.json", "stage_basis_path": "final.json",
            "transformation_path": "transform.json",
            "first_success_beta": None, "first_success_tour": 0, "first_success_stage": "lll",
            "stages": [{"stage_id": "lll", "kind": "lll", "beta": None, "tour": 0,
                        "stage_wall_seconds": 0.1, "scan_wall_seconds": 0.01,
                        "cumulative_wall_seconds": 0.12, "minimum_norm_sq": 3, "candidate_count": 1}],
            "found_candidate": {"f": list(self.truth["f"]), "g": list(self.truth["g"]),
                                "vector": list(self.rows[0]), "norm_sq": 3,
                                "row_index": 0, "sign": 1, "stage_id": "lll", "beta": None, "tour": 0}}
        self.save()

    def save(self) -> None:
        _write(self.paths["public"], self.public)
        self.truth["public_sha256"] = _sha(self.paths["public"])
        _write(self.paths["truth"], self.truth)
        _write(self.paths["config"], self.config)
        self.result["public_sha256"] = _sha(self.paths["public"])
        self.result["config_sha256"] = _sha(self.paths["config"])
        _write(self.paths["result"], self.result)
        _write(self.trial / "initial.json", {"rows": self.initial})
        _write(self.trial / "final.json", {"rows": self.rows})
        _write(self.trial / "transform.json", {"rows": self.transform})

    def audit(self, exact: bool = True) -> dict:
        return audit_trial(self.paths["public"], self.paths["truth"], self.paths["config"],
                           self.paths["result"], exact_determinant=exact)

    def make_dense_no_recovery(self) -> None:
        self.public.update(h=[1] + [0] * 15,
                           distribution={"kind": "fixed_weight_ternary", "w_f": 16, "w_g": 16})
        self.truth.update(f=[1] * 16, g=[1] * 16)
        self.config.update(w_f=16, w_g=16)
        self.initial = initial_basis(self.public, self.pattern)
        self.rows = [list(row) for row in self.initial]
        self.transform = [[int(i == j) for j in range(31)] for i in range(31)]
        self.result.update(status="interrupted", found_candidate=None,
                           first_success_beta=None, first_success_tour=None, first_success_stage=None)
        self.result["stages"][0].update(candidate_count=0, minimum_norm_sq=2)
        self.save()

    def test_valid_recovery_certifies_pattern_lattice_exactly(self) -> None:
        report = self.audit()
        self.assertTrue(report["audit_passed"], report["failed_checks"])
        self.assertTrue(report["strict_lattice_equivalence_certified"])
        self.assertEqual(report["parameters"]["dimension"], 31)
        self.assertEqual(report["basis"]["exact_transformation_determinant"], 1)
        self.assertEqual(report["equivalence"], {"shift": 0, "sign": 1})
        self.assertEqual(report["pattern_compatible_truth_rotation_count"], 15)
        self.assertEqual(report["pattern_coverage"]["compatible_signed_representatives"], 30)

    def test_constructor_uses_removed_coordinate_and_negacyclic_sign(self) -> None:
        public = {"n": 3, "q": 7, "h": [1, 2, 3]}
        self.assertEqual(initial_basis(public, [1]),
                         [[7, 0, 0, 0, 0], [0, 7, 0, 0, 0], [0, 0, 7, 0, 0],
                          [1, 2, 3, 1, 0], [5, 4, 1, 0, 1]])

    def test_proper_sublattice_fails_despite_correct_candidate(self) -> None:
        self.rows[1] = [2 * v for v in self.rows[1]]
        self.transform[1] = [2 * v for v in self.transform[1]]
        self.save()
        report = self.audit()
        self.assertIn("transformation_exact_unimodular", report["failed_checks"])
        self.assertEqual(abs(report["basis"]["exact_transformation_determinant"]), 2)

    def test_saved_transform_tampering_is_rejected(self) -> None:
        self.transform[0][0] += 1
        self.save()
        self.assertIn("exact_saved_transformation_relation", self.audit()["failed_checks"])

    def test_nonlattice_final_row_is_rejected(self) -> None:
        self.rows[1][0] += 1
        self.save()
        self.assertIn("all_final_rows_lattice_members", self.audit()["failed_checks"])

    def test_wrong_initial_basis_is_rejected(self) -> None:
        self.initial[-1][0] += 1
        self.save()
        self.assertIn("initial_basis_exact_construction", self.audit()["failed_checks"])

    def test_full_f_cannot_disagree_with_pattern_or_saved_vector(self) -> None:
        # A simultaneous rotated secret satisfies the public key but violates P.
        candidate = self.result["found_candidate"]
        candidate.update(f=[0] * 15 + [1], g=[-1] + [0] * 14 + [1])
        self.save()
        report = self.audit()
        self.assertTrue(report["candidate_validation"]["valid"])
        self.assertIn("candidate_obeys_pattern", report["failed_checks"])
        self.assertIn("candidate_vector_reconstruction", report["failed_checks"])

    def test_candidate_must_be_a_row_of_the_saved_basis(self) -> None:
        self.result["found_candidate"]["row_index"] = 1
        self.save()
        self.assertIn("candidate_saved_basis_row", self.audit()["failed_checks"])

    def test_invalid_coordinate_partition_is_reported_without_crashing(self) -> None:
        self.result["coordinates"]["P"] = [15, 15]
        self.save()
        self.assertIn("pattern_coordinates", self.audit()["failed_checks"])
        self.result["coordinates"].update(P=[15], C=list(range(14)), T=[14])
        self.save()
        report = self.audit()
        self.assertIn("complement_coordinates", report["failed_checks"])
        self.assertIn("no_tail_coordinates", report["failed_checks"])

    def test_lll_only_failure_and_zero_truth_coverage_are_valid(self) -> None:
        self.make_dense_no_recovery()
        report = self.audit()
        self.assertTrue(report["audit_passed"], report["failed_checks"])
        self.assertTrue(report["strict_lattice_equivalence_certified"])
        self.assertEqual(report["classification"], "censored_no_recovery_observed")
        self.assertFalse(report["pattern_coverage"]["secret_orbit_available"])

    def test_latest_completed_artifact_fallback_on_interruption(self) -> None:
        self.make_dense_no_recovery()
        self.result["latest_complete_basis_path"] = self.result.pop("stage_basis_path")
        self.result["latest_complete_transform_path"] = self.result.pop("transformation_path")
        self.save()
        report = self.audit()
        self.assertTrue(report["audit_passed"], report["failed_checks"])
        self.assertTrue(report["completed_stage_evidence"])

    def test_initial_only_interruption_reports_incomplete_evidence(self) -> None:
        self.make_dense_no_recovery()
        self.result["stages"] = []
        self.result.pop("stage_basis_path")
        self.result.pop("transformation_path")
        self.save()
        report = self.audit()
        self.assertTrue(report["audit_passed"], report["failed_checks"])
        self.assertTrue(report["integrity_passed"])
        self.assertFalse(report["audit_complete"])
        self.assertFalse(report["trial_complete"])
        self.assertFalse(report["completed_stage_evidence"])
        self.assertFalse(report["strict_lattice_equivalence_certified"])
        self.assertEqual(report["evidence_status"], "no_completed_reduction_stage")

    def test_running_checkpoint_is_censored_without_claiming_trial_completion(self) -> None:
        self.make_dense_no_recovery()
        self.result["status"] = "running"
        self.save()
        report = self.audit()
        self.assertTrue(report["audit_passed"], report["failed_checks"])
        self.assertTrue(report["strict_lattice_equivalence_certified"])
        self.assertTrue(report["censored"])
        self.assertFalse(report["trial_complete"])
        self.assertEqual(report["classification"], "censored_no_recovery_observed")

    def test_public_valid_other_secret_is_success_without_truth_equivalence(self) -> None:
        self.public.update(h=[1] + [0] * 15,
                           distribution={"kind": "fixed_weight_ternary", "w_f": 2, "w_g": 2})
        self.config.update(w_f=2, w_g=2)
        self.truth.update(f=[1, 1] + [0] * 14, g=[1, 1] + [0] * 14)
        self.initial = initial_basis(self.public, self.pattern)
        self.rows = [list(row) for row in self.initial]
        self.transform = [[int(i == j) for j in range(31)] for i in range(31)]
        self.rows[16] = [a + b for a, b in zip(self.rows[16], self.rows[18])]
        self.transform[16][18] = 1
        secret = [1, 0, 1] + [0] * 13
        self.result["found_candidate"].update(f=secret, g=secret, row_index=16,
                                              vector=list(self.rows[16]), norm_sq=4)
        self.result["stages"][0]["minimum_norm_sq"] = 2
        self.save()
        report = self.audit()
        self.assertTrue(report["audit_passed"], report["failed_checks"])
        self.assertEqual(report["classification"], "other_valid_short_secret")
        self.assertIsNone(report["equivalence"])

    def test_schedule_skipping_second_tour_is_rejected(self) -> None:
        lll = self.result["stages"][0]
        lll["candidate_count"] = 0
        self.result["stages"] = [lll] + [
            {**lll, "stage_id": f"b{beta}", "kind": "bkz", "beta": beta,
             "tour": 1, "candidate_count": int(beta == 31), "cumulative_wall_seconds": i + 1}
            for i, beta in enumerate((30, 31))]
        self.result.update(first_success_beta=31, first_success_tour=1, first_success_stage="bkz")
        self.result["found_candidate"].update(stage_id="b31", beta=31, tour=1)
        self.save()
        self.assertIn("tour2_stage_2_beta_and_tour", self.audit()["failed_checks"])

    def test_modular_audit_never_claims_exact_proof(self) -> None:
        report = self.audit(exact=False)
        self.assertTrue(report["audit_passed"], report["failed_checks"])
        self.assertFalse(report["strict_lattice_equivalence_certified"])
        self.assertEqual(report["basis"]["unimodularity_evidence"], "modular_only_not_an_integer_proof")

    def test_candidate_cannot_be_ignored_in_no_recovery_result(self) -> None:
        self.result.update(status="interrupted", found_candidate=None,
                           first_success_beta=None, first_success_tour=None, first_success_stage=None)
        self.result["stages"][0]["candidate_count"] = 0
        self.save()
        self.assertIn("last_stage_public_scan_outcome", self.audit()["failed_checks"])

    def test_coverage_counts_signs_without_treating_them_as_independent_shifts(self) -> None:
        report = orbit_coverage({"f": [1, 0, 0], "g": [1, 1, 0]}, [0])
        self.assertEqual(report["compatible_shifts"], [1, 2])
        self.assertEqual(report["compatible_signed_representatives"], 4)

    def test_cli_writes_exact_audit(self) -> None:
        output = self.trial / "custom_audit.json"
        argv = [token for name, path in self.paths.items() for token in (f"--{name}", str(path))]
        code = main(argv + ["--output", str(output)])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(output.read_text())["strict_lattice_equivalence_certified"])


if __name__ == "__main__":
    unittest.main()
