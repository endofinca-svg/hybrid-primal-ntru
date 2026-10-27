"""Small exact fixtures test proof obligations, not reduction behavior."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from .evaluate_baseline import determinant_exact, evaluate_files, initial_basis


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n")


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class BaselineAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.public = {"schema": "ntru-e2e-public-v1", "instance_id": "fixture",
                       "n": 2, "q": 17, "ring": "x^n+1", "h": [1, 1],
                       "distribution": {"kind": "fixed_weight_ternary", "w_f": 1, "w_g": 2}}
        _write(self.root / "public/instance_01.json", self.public)
        _write(self.root / "private/instance_01.truth.json",
               {"instance_id": "fixture", "f": [1, 0], "g": [1, 1],
                "public_sha256": _hash(self.root / "public/instance_01.json")})
        _write(self.root / "baseline/config.json", {"bkz_start": 30, "jump": 1})
        self.initial = initial_basis(self.public)
        self.rows = [self.initial[i] for i in (2, 0, 1, 3)]
        self.transform = [[int(j == i) for j in range(4)] for i in (2, 0, 1, 3)]
        self.result = {
            "schema": "fixture", "status": "success", "dimension": 4,
            "public_sha256": _hash(self.root / "public/instance_01.json"),
            "config_sha256": _hash(self.root / "baseline/config.json"),
            "initial_basis_path": "initial_basis.json", "stage_basis_path": "success_basis.json",
            "transformation_path": "success_transform.json",
            "stages": [{"stage_id": "lll", "kind": "lll", "beta": None,
                        "stage_wall_seconds": 0.1, "scan_wall_seconds": 0.01,
                        "cumulative_wall_seconds": 0.12, "minimum_norm_sq": 3,
                        "candidate_count": 1}],
            "found_candidate": {"f": [1, 0], "g": [1, 1], "vector": [1, 1, 1, 0],
                                "norm_sq": 3, "row_index": 0, "sign": 1,
                                "stage_id": "lll", "beta": None},
        }
        self.save()

    def save(self) -> None:
        for name, rows in (("initial_basis", self.initial), ("success_basis", self.rows),
                           ("success_transform", self.transform)):
            _write(self.root / f"baseline/{name}.json", {"rows": rows})
        _write(self.root / "baseline/result.json", self.result)

    def audit(self) -> dict:
        return evaluate_files(self.root, exact_determinant=True)

    def test_valid_recovery_certifies_exact_lattice_and_equivalence(self) -> None:
        report = self.audit()
        self.assertTrue(report["audit_passed"], report["failed_checks"])
        self.assertTrue(report["strict_lattice_equivalence_certified"])
        self.assertEqual(report["equivalence"], {"shift": 0, "sign": 1})
        self.assertEqual(report["basis"]["exact_transformation_determinant"], 1)

    def test_negative_rotation_is_accepted(self) -> None:
        # Row 3 of the initial basis has the signed rotation modulo q, so make
        # its exact centered representative using an allowed row operation.
        self.rows[3] = [-1, 1, 0, 1]
        self.transform[3] = [-1, 0, 0, 1]
        self.result["found_candidate"].update(f=[0, -1], g=[1, -1],
                                              vector=[1, -1, 0, -1], row_index=3, sign=-1)
        self.save()
        report = self.audit()
        self.assertTrue(report["audit_passed"], report["failed_checks"])
        self.assertEqual(report["equivalence"], {"shift": 1, "sign": -1})

    def test_proper_sublattice_is_rejected_despite_valid_secret_row(self) -> None:
        self.rows[1] = [2 * v for v in self.rows[1]]
        self.transform[1] = [2 * v for v in self.transform[1]]
        self.save()
        report = self.audit()
        self.assertFalse(report["audit_passed"])
        self.assertIn("transformation_exact_unimodular", report["failed_checks"])
        self.assertEqual(abs(report["basis"]["exact_transformation_determinant"]), 2)

    def test_tampered_saved_transform_is_rejected(self) -> None:
        self.transform[0][0] += 1
        self.save()
        self.assertIn("saved_transformation_matches_unique_coefficients", self.audit()["failed_checks"])

    def test_nonmember_basis_row_is_rejected(self) -> None:
        self.rows[1] = list(self.rows[1])
        self.rows[1][0] += 1
        self.save()
        self.assertIn("all_final_rows_lattice_members", self.audit()["failed_checks"])

    def test_public_hash_and_candidate_row_tampering_are_rejected(self) -> None:
        self.result["public_sha256"] = "0" * 64
        self.result["found_candidate"]["row_index"] = 1
        self.save()
        report = self.audit()
        self.assertIn("result_public_hash", report["failed_checks"])
        self.assertIn("candidate_saved_basis_row", report["failed_checks"])

    def test_false_public_relation_is_rejected(self) -> None:
        self.result["found_candidate"]["g"] = [1, -1]
        self.save()
        report = self.audit()
        self.assertFalse(report["candidate_validation"]["valid"])
        self.assertEqual(report["candidate_validation"]["reason"], "public_relation")

    def test_multiple_tours_then_unit_beta_jump_are_accepted(self) -> None:
        # Enlarge the exact fixture enough to admit block sizes 30 and 31;
        # reduction is not run, only the saved schedule and proof are audited.
        self.public.update(n=16, h=[1, 1] + [0] * 14)
        _write(self.root / "public/instance_01.json", self.public)
        _write(self.root / "private/instance_01.truth.json",
               {"instance_id": "fixture", "f": [1] + [0] * 15,
                "g": [1, 1] + [0] * 14,
                "public_sha256": _hash(self.root / "public/instance_01.json")})
        self.initial = initial_basis(self.public)
        order = [16] + list(range(16)) + list(range(17, 32))
        self.rows = [self.initial[i] for i in order]
        self.transform = [[int(j == i) for j in range(32)] for i in order]
        self.result.update(dimension=32, public_sha256=_hash(self.root / "public/instance_01.json"))
        first = self.result["stages"][0]
        first["candidate_count"] = 0
        self.result["stages"] = [first] + [
            {**first, "stage_id": f"bkz_b{beta}_t{tour}", "kind": "bkz", "beta": beta,
             "tour": tour, "candidate_count": int(index == 3), "cumulative_wall_seconds": index + 0.2}
            for index, (beta, tour) in enumerate(((30, 1), (30, 2), (31, 1)), start=1)]
        self.result["found_candidate"].update(
            f=[1] + [0] * 15, g=[1, 1] + [0] * 14,
            vector=self.rows[0], beta=31, tour=1, stage_id="bkz_b31_t1")
        self.save()
        report = self.audit()
        self.assertTrue(report["audit_passed"], report["failed_checks"])
        self.result["stages"][2]["tour"] = 4
        self.save()
        self.assertIn("stage_2_kind_and_beta", self.audit()["failed_checks"])

    def test_exact_determinant_handles_zero_pivot_and_singularity(self) -> None:
        self.assertEqual(determinant_exact([[0, 1], [1, 0]]), -1)
        self.assertEqual(determinant_exact([[1, 2], [2, 4]]), 0)
        self.assertEqual(determinant_exact([[2, 3, 1], [4, 1, 7], [5, 2, 0]]), 80)


if __name__ == "__main__":
    unittest.main()
