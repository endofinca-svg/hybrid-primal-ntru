"""Exact toy fixtures and tampered schedules for the two-tour audit."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from .evaluate_baseline import initial_basis
from .evaluate_baseline_tour2 import audit_protocol, evaluate_files, main


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n")


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _config() -> dict:
    return {"bkz_start": 30, "bkz_stop": 32, "bkz_jump": 1,
            "bkz_max_tours_per_beta": 2, "bkz_clean_stops_current_beta": False}


def _result(schedule: tuple[tuple[int, int], ...] = ((30, 1), (30, 2), (31, 1))) -> dict:
    stages = [{"stage_id": "lll", "kind": "lll", "beta": None, "tour": 0,
               "candidate_count": 0, "stage_wall_seconds": 0.1,
               "scan_wall_seconds": 0.01, "cumulative_wall_seconds": 0.12,
               "minimum_norm_sq": 3}]
    for index, (beta, tour) in enumerate(schedule, start=1):
        stages.append({**stages[0], "stage_id": f"bkz_b{beta:03d}_t{tour:02d}",
                       "kind": "bkz", "beta": beta, "tour": tour,
                       "clean": True, "cumulative_wall_seconds": index + 0.2})
    stages[-1]["candidate_count"] = 1
    final = stages[-1]
    return {"dimension": 32, "status": "success", "stages": stages,
            "first_success_beta": final["beta"], "first_success_tour": final["tour"],
            "first_success_stage": final["kind"],
            "found_candidate": {"stage_id": final["stage_id"], "beta": final["beta"],
                                "tour": final["tour"]}}


class Tour2ProtocolTests(unittest.TestCase):
    def test_success_on_first_or_second_tour_after_completed_beta(self) -> None:
        for schedule in (((30, 1),), ((30, 1), (30, 2)),
                         ((30, 1), (30, 2), (31, 1)),
                         ((30, 1), (30, 2), (31, 1), (31, 2))):
            with self.subTest(schedule=schedule):
                report = audit_protocol(_config(), _result(schedule))
                self.assertTrue(report["protocol_passed"], report["failed_checks"])

    def test_lll_success_needs_no_bkz(self) -> None:
        report = audit_protocol(_config(), _result(()))
        self.assertTrue(report["protocol_passed"], report["failed_checks"])

    def test_earlier_eight_tour_policy_is_rejected(self) -> None:
        config = _config()
        config.update(bkz_max_tours_per_beta=8, bkz_clean_stops_current_beta=True)
        report = audit_protocol(config, _result())
        self.assertIn("tour2_config_max_tours", report["failed_checks"])
        self.assertIn("tour2_config_clean_does_not_stop", report["failed_checks"])

    def test_skip_second_tour_is_rejected_even_when_clean(self) -> None:
        report = audit_protocol(_config(), _result(((30, 1), (31, 1))))
        self.assertIn("tour2_stage_2_beta_and_tour", report["failed_checks"])

    def test_third_tour_and_beta_jump_are_rejected(self) -> None:
        for schedule in (((30, 1), (30, 2), (30, 3)),
                         ((30, 1), (30, 2), (32, 1)), ((31, 1),)):
            with self.subTest(schedule=schedule):
                self.assertFalse(audit_protocol(_config(), _result(schedule))["protocol_passed"])

    def test_first_success_metadata_and_candidate_tour_are_checked(self) -> None:
        for field, value in (("first_success_beta", 30), ("first_success_tour", 2),
                             ("first_success_stage", "lll")):
            result = _result()
            result[field] = value
            with self.subTest(field=field):
                self.assertIn("tour2_" + field, audit_protocol(_config(), result)["failed_checks"])
        result = _result()
        result["found_candidate"]["tour"] = 2
        self.assertIn("tour2_candidate_final_metadata", audit_protocol(_config(), result)["failed_checks"])

    def test_earlier_candidate_cannot_be_ignored(self) -> None:
        result = _result()
        result["stages"][1]["candidate_count"] = 1
        self.assertIn("tour2_stage_1_candidate_count", audit_protocol(_config(), result)["failed_checks"])

    def test_boolean_tours_or_limits_are_rejected(self) -> None:
        result = _result(((30, 1),))
        result["stages"][1]["tour"] = True
        self.assertFalse(audit_protocol(_config(), result)["protocol_passed"])
        config = _config()
        config["bkz_max_tours_per_beta"] = True
        self.assertFalse(audit_protocol(config, _result())["protocol_passed"])

    def test_exhaustion_requires_two_tours_and_configured_stop(self) -> None:
        result = _result(((30, 1), (30, 2), (31, 1), (31, 2), (32, 1), (32, 2)))
        result.update(status="exhausted", found_candidate=None)
        for field in ("first_success_beta", "first_success_tour", "first_success_stage"):
            result.pop(field)
        result["stages"][-1]["candidate_count"] = 0
        self.assertTrue(audit_protocol(_config(), result)["protocol_passed"])
        result["stages"].pop()
        self.assertIn("tour2_exhausted_final_beta_completed", audit_protocol(_config(), result)["failed_checks"])

    def test_interrupt_can_leave_only_first_tour_of_last_beta(self) -> None:
        result = _result()
        result.update(status="interrupted", found_candidate=None)
        for field in ("first_success_beta", "first_success_tour", "first_success_stage"):
            result.pop(field)
        result["stages"][-1]["candidate_count"] = 0
        report = audit_protocol(_config(), result)
        self.assertTrue(report["protocol_passed"], report["failed_checks"])


class Tour2AuditIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        n = 16
        public = {"schema": "ntru-e2e-public-v1", "instance_id": "tour2-fixture",
                  "n": n, "q": 17, "ring": "x^n+1", "h": [1, 1] + [0] * (n - 2),
                  "distribution": {"kind": "fixed_weight_ternary", "w_f": 1, "w_g": 2}}
        _write(self.root / "public/instance_01.json", public)
        _write(self.root / "private/instance_01.truth.json",
               {"instance_id": public["instance_id"], "f": [1] + [0] * (n - 1),
                "g": [1, 1] + [0] * (n - 2), "public_sha256": _hash(self.root / "public/instance_01.json")})
        _write(self.root / "baseline_tour2/config.json", _config())
        initial = initial_basis(public)
        order = [n] + list(range(n)) + list(range(n + 1, 2 * n))
        rows = [initial[i] for i in order]
        transform = [[int(i == j) for j in range(2 * n)] for i in order]
        for name, matrix in (("initial_basis", initial), ("success_basis", rows),
                             ("success_transform", transform)):
            _write(self.root / f"baseline_tour2/{name}.json", {"rows": matrix})
        self.result = _result()
        self.result.update(public_sha256=_hash(self.root / "public/instance_01.json"),
                           config_sha256=_hash(self.root / "baseline_tour2/config.json"),
                           initial_basis_path="initial_basis.json", stage_basis_path="success_basis.json",
                           transformation_path="success_transform.json")
        self.result["found_candidate"].update(f=[1] + [0] * (n - 1), g=[1, 1] + [0] * (n - 2),
                                              vector=rows[0], row_index=0, sign=1, norm_sq=3)
        _write(self.root / "baseline_tour2/result.json", self.result)

    def test_default_tour2_paths_reuse_exact_mathematical_proof(self) -> None:
        report = evaluate_files(self.root, exact_determinant=True)
        self.assertTrue(report["audit_passed"], report["failed_checks"])
        self.assertTrue(report["strict_lattice_equivalence_certified"])
        self.assertEqual(report["basis"]["exact_transformation_determinant"], 1)
        self.assertEqual(report["tour2_protocol"]["completed_bkz_tours"], 3)

    def test_valid_math_does_not_hide_schedule_tampering(self) -> None:
        self.result["first_success_tour"] = 2
        _write(self.root / "baseline_tour2/result.json", self.result)
        report = evaluate_files(self.root, exact_determinant=True)
        self.assertTrue(report["strict_lattice_equivalence_certified"])
        self.assertFalse(report["audit_passed"])
        self.assertIn("tour2_first_success_tour", report["failed_checks"])

    def test_path_overrides_and_cli_output(self) -> None:
        replacement = self.root / "alternative"
        replacement.mkdir()
        for name in ("config", "result"):
            (replacement / f"{name}.json").write_bytes((self.root / f"baseline_tour2/{name}.json").read_bytes())
        # Saved checkpoint paths are relative to the selected result directory.
        alternate = copy.deepcopy(self.result)
        for field in ("initial_basis_path", "stage_basis_path", "transformation_path"):
            alternate[field] = str(self.root / "baseline_tour2" / alternate[field])
        _write(replacement / "result.json", alternate)
        output = replacement / "custom-audit.json"
        status = main(["--run-dir", str(self.root), "--config", str(replacement / "config.json"),
                       "--result", str(replacement / "result.json"), "--output", str(output),
                       "--exact-determinant"])
        self.assertEqual(status, 0)
        self.assertTrue(json.loads(output.read_text())["audit_passed"])


if __name__ == "__main__":
    unittest.main()
