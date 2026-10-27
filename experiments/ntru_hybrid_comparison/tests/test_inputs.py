"""Public input-package algebra and integrity checks, with no reduction calls."""

import copy
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiments.ntru_e2e import algebra
from experiments.ntru_hybrid_comparison.candidates import CandidatePolicy
from experiments.ntru_hybrid_comparison.id_mapping import target_sha256
from experiments.ntru_hybrid_comparison.inputs import (
    attach_reduction,
    build_input_bundle,
    create_working_copy,
    load_input_bundle,
    save_input_bundle,
)
from experiments.ntru_hybrid_comparison.validation import validate_public_candidate


def public_fixture():
    """The fixed public h=x example from the phase 2A algebra tests."""
    return {
        "schema": "ntru-e2e-public-v1",
        "instance_id": "phase2b-manual-package-only",
        "n": 8,
        "q": 17,
        "ring": "x^n+1",
        "h": [0, 1, 0, 0, 0, 0, 0, 0],
        "distribution": {"kind": "fixed_weight_ternary", "w_f": 4, "w_g": 4},
    }


def make_bundle():
    return build_input_bundle(
        public_fixture(), [1, 4], [6, 2, 5, 3], [7, 0],
        CandidatePolicy("full_nonzero"),
        remaining_positions=[0, 2, 3, 5, 6, 7],
        provenance={"kind": "test_fixture"},
    )


def canonical_sha256(value):
    return sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False).encode("ascii")).hexdigest()


def write_rehashed_envelope(path, bundle):
    """Bypass the outer checksum deliberately to exercise semantic checks."""
    envelope = {
        "schema": "ntru-paired-input-envelope-v1", "complete": True,
        "payload": bundle, "payload_sha256": canonical_sha256(bundle),
    }
    path.write_text(json.dumps(envelope), encoding="utf-8")


class PublicInputPackageTests(unittest.TestCase):
    def test_original_only_roundtrip_preserves_all_public_algebra_and_counts(self):
        bundle = make_bundle()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "public-input.json"
            save_input_bundle(path, bundle)
            loaded = load_input_bundle(path)
            self.assertEqual(bundle, loaded)
            self.assertEqual(loaded["coordinates"], bundle["coordinates"])
            self.assertEqual(loaded["coordinates"]["remaining"], [0, 2, 3, 5, 6, 7])
            self.assertEqual(loaded["coordinates"]["C"], [6, 2, 5, 3])
            self.assertEqual(loaded["coordinates"]["T"], [7, 0])
            self.assertEqual(loaded["dimensions"],
                             {"n": 8, "q": 17, "k": 2, "kappa": 2, "d": 12,
                              "d_pattern": 14})
            self.assertEqual(loaded["basis"]["status"], "original_only")
            self.assertIsNone(loaded["basis"]["reduced"])
            self.assertIsNone(loaded["basis"]["transform"])
            self.assertEqual(len(loaded["targets"]), 8)
            counts = loaded["candidate_batch"]["counts"]
            self.assertEqual(counts["exact_unique_count"], 8)
            self.assertEqual(counts["submitted_count"], 0)
            self.assertEqual(counts["completed_count"], 0)
            self.assertIsNone(counts["projection_group_count"])
            with self.assertRaises(ValueError):
                load_input_bundle(path, require_reduced_verified=True)
            with self.assertRaises(ValueError):
                create_working_copy(loaded)

    def test_centered_target_translation_and_matched_reconstruction(self):
        bundle = make_bundle()
        index = next(row["target_index"] for row in bundle["id_mapping"]
                     if row["u"] == [-1, 1])
        target = bundle["targets"][index]
        self.assertEqual(target["raw"], [16, -1] + [0] * 10)
        self.assertEqual(target["used"], [-1, -1] + [0] * 10)
        self.assertEqual(target["used_minus_raw"], [-17] + [0] * 11)
        coefficients = target["translation_coefficients"]
        rows = bundle["basis"]["original"]
        translation = [sum(coefficient * row[column]
                           for coefficient, row in zip(coefficients, rows))
                       for column in range(12)]
        self.assertEqual(translation, target["used_minus_raw"])
        self.assertTrue(any(translation))

        # These hand-written short vectors are an algebra oracle, not a decoder
        # output, key generation, or secret-dependent input selection.
        f = [1, 0, 1, 0, 0, -1, 0, -1]
        g = [1, 1, 0, 1, 0, 0, -1, 0]
        core = [f[position] for position in bundle["coordinates"]["C"]]
        error = g + core
        vector_used = [a + b for a, b in zip(target["used"], error)]
        vector_raw = [a - b for a, b in zip(vector_used, translation)]
        P, C, T = [bundle["coordinates"][key] for key in ("P", "C", "T")]
        for vector, actual_target in ((vector_used, target["used"]),
                                      (vector_raw, target["raw"])):
            reconstructed = algebra.reconstruct(
                vector, actual_target, P, C, T, [-1, 1], 8)
            self.assertEqual(reconstructed, {"f": f, "g": g})
        wrong = algebra.reconstruct(
            vector_used, target["raw"], P, C, T, [-1, 1], 8)
        self.assertFalse(validate_public_candidate(public_fixture(), **wrong)["public_valid"])

    def test_ordered_tail_changes_identity_and_target_interpretation(self):
        first = make_bundle()
        second = build_input_bundle(
            public_fixture(), [1, 4], [6, 2, 5, 3], [0, 7],
            CandidatePolicy("full_nonzero"),
            remaining_positions=[0, 2, 3, 5, 6, 7],
            provenance={"kind": "test_fixture"},
        )
        self.assertNotEqual(first["candidate_batch"]["ordered_sha256"],
                            second["candidate_batch"]["ordered_sha256"])
        self.assertNotEqual([row["candidate_sha_id"] for row in first["id_mapping"]],
                            [row["candidate_sha_id"] for row in second["id_mapping"]])
        self.assertNotEqual([row["used"] for row in first["targets"]],
                            [row["used"] for row in second["targets"]])
        self.assertEqual(first["basis"]["original"], second["basis"]["original"])

    def test_invalid_partitions_are_rejected(self):
        for P, C, T, remaining in (
            ([1, 4], [6, 2, 5, 1], [7, 0], None),  # P/core overlap.
            ([1, 4], [6, 2, 5], [7, 0], None),  # Missing coordinate 3.
            ([1, 4], [6, 2, 5, 3], [7, 7], None),  # Duplicate tail.
            ([1, 4], [6, 2, 5, 3], [7, 0], [0, 2, 3, 5, 6, 6]),
            ([1, True], [6, 2, 5, 3], [7, 0], None),
            ([1, 4], [6, 2, 5, 3], {7, 0}, None),
        ):
            with self.subTest(P=P, C=C, T=T, remaining=remaining):
                with self.assertRaises(ValueError):
                    build_input_bundle(
                        public_fixture(), P, C, T, CandidatePolicy("full_nonzero"),
                        remaining_positions=remaining,
                        provenance={"kind": "test_fixture"})

    def test_semantic_tampering_rejected_even_after_rehashing_envelope(self):
        original = make_bundle()

        def tamper_basis(bundle):
            bundle["basis"]["original"][0][0] += 1
            bundle["basis"]["original_sha256"] = canonical_sha256(bundle["basis"]["original"])

        def tamper_target(bundle):
            bundle["targets"][0]["used"][0] += 17
            digest = target_sha256(bundle["targets"][0]["used"])
            bundle["targets"][0]["used_sha256"] = digest
            bundle["id_mapping"][0]["target_sha256"] = digest

        mutations = {
            "public_constructed_basis": tamper_basis,
            "actual_target_relation": tamper_target,
            "coordinate_partition": lambda b: b["coordinates"]["T"].reverse(),
            "candidate_order": lambda b: b["candidate_batch"]["candidates"].reverse(),
            "integer_id_map": lambda b: b["id_mapping"][0].update(backend_integer_id=99),
            "basis_digest": lambda b: b["basis"].update(original_sha256="0" * 64),
            "forged_reduction_status": lambda b: b["basis"].update(status="reduced_verified"),
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tampered.json"
            for name, mutate in mutations.items():
                with self.subTest(mutation=name):
                    changed = copy.deepcopy(original)
                    mutate(changed)
                    write_rehashed_envelope(path, changed)
                    with self.assertRaises(ValueError):
                        load_input_bundle(path)

    def test_partial_file_and_untrusted_envelope_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "incomplete.json"
            path.write_text('{"schema": "ntru-paired-input-envelope-v1", "payload":',
                            encoding="utf-8")
            with self.assertRaises(ValueError):
                load_input_bundle(path)
            save_input_bundle(path, make_bundle())
            envelope = json.loads(path.read_text(encoding="utf-8"))
            for key, value in (("complete", False), ("payload_sha256", "0" * 64)):
                with self.subTest(field=key):
                    changed = copy.deepcopy(envelope)
                    changed[key] = value
                    path.write_text(json.dumps(changed), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        load_input_bundle(path)

    def test_atomic_publish_failure_preserves_existing_complete_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "atomic-input.json"
            bundle = make_bundle()
            save_input_bundle(path, bundle)
            before = path.read_bytes()
            replacement = copy.deepcopy(bundle)
            replacement["provenance"]["code_version"] = "synthetic-atomic-test"
            with patch("experiments.ntru_hybrid_comparison.inputs.os.replace",
                       side_effect=OSError("simulated atomic rename failure")):
                with self.assertRaises(OSError):
                    save_input_bundle(path, replacement)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(load_input_bundle(path), bundle)
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_independent_loads_and_working_copies_do_not_share_mutable_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "independent.json"
            bundle = make_bundle()
            save_input_bundle(path, bundle)
            first, second = load_input_bundle(path), load_input_bundle(path)
            first["basis"]["original"][0][0] += 3
            first["targets"][0]["used"][0] += 4
            first["id_mapping"][0]["u"][0] = 999
            self.assertEqual(second, bundle)
            self.assertEqual(load_input_bundle(path), bundle)
            work_a = create_working_copy(second, use_reduced=False)
            work_b = create_working_copy(second, use_reduced=False)
            self.assertIsNone(work_a["gso"])
            work_a["rows"][0][0] += 5
            work_a["original_rows"][0][0] += 7
            work_a["id_mapping"][0]["u"][0] = 888
            work_a["targets"][0][0] += 9
            self.assertEqual(work_b["rows"], bundle["basis"]["original"])
            self.assertEqual(work_b["original_rows"], bundle["basis"]["original"])
            self.assertEqual(work_b["id_mapping"], bundle["id_mapping"])
            self.assertEqual(work_b["targets"], [row["used"] for row in bundle["targets"]])
            self.assertEqual(second, bundle)
            self.assertEqual(load_input_bundle(path), bundle)

    def test_large_integer_unimodular_certificate_survives_save_load(self):
        bundle = make_bundle()
        rows = bundle["basis"]["original"]
        dimension = len(rows)
        huge = 2 ** 80 + 19
        transform = [[int(i == j) for j in range(dimension)]
                     for i in range(dimension)]
        transform[0][1] = huge
        reduced = copy.deepcopy(rows)
        reduced[0] = [a + huge * b for a, b in zip(rows[0], rows[1])]
        execution = {
            "original_basis": copy.deepcopy(rows),
            "reduced_basis": reduced,
            "transform": transform,
            "protocol": copy.deepcopy(bundle["basis"]["protocol"]),
            "record": {
                "status": "completed", "elapsed_seconds": 0.0,
                "cumulative_seconds": 0.0, "stages": [],
                "timing_source": "synthetic_test_fixture_not_a_backend_measurement",
            },
        }
        attached = attach_reduction(bundle, execution)
        self.assertEqual(bundle["basis"]["status"], "original_only")
        self.assertEqual(attached["basis"]["status"], "reduced_verified")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large-exact-transform.json"
            save_input_bundle(path, attached)
            loaded = load_input_bundle(path, require_reduced_verified=True)
            self.assertEqual(loaded["basis"]["transform"][0][1], huge)
            self.assertEqual(loaded["basis"]["reduced"], reduced)
            self.assertGreater(loaded["basis"]["reduced"][0][1], 2 ** 53)
            self.assertIsInstance(loaded["basis"]["reduced"][0][1], int)
            working = create_working_copy(loaded)
            self.assertEqual(working["rows"], reduced)
            self.assertIsNone(working["gso"])

    def test_missing_checkpoint_certificate_requires_explicit_quarantine(self):
        bundle = make_bundle()
        execution = {
            "original_basis": copy.deepcopy(bundle["basis"]["original"]),
            "reduced_basis": copy.deepcopy(bundle["basis"]["original"]),
            "transform": None,
            "protocol": copy.deepcopy(bundle["basis"]["protocol"]),
            "record": {
                "status": "completed", "elapsed_seconds": 0.0,
                "cumulative_seconds": 0.0, "stages": [],
                "timing_source": "synthetic_test_fixture_not_a_backend_measurement",
            },
        }
        with self.assertRaises(ValueError):
            attach_reduction(bundle, execution)
        quarantined = attach_reduction(bundle, execution, allow_unverified=True)
        self.assertEqual(quarantined["basis"]["status"], "reduced_unverified")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unverified-checkpoint.json"
            save_input_bundle(path, quarantined)
            self.assertEqual(load_input_bundle(path), quarantined)
            with self.assertRaises(ValueError):
                load_input_bundle(path, require_reduced_verified=True)
            with self.assertRaises(ValueError):
                create_working_copy(quarantined)


if __name__ == "__main__":
    unittest.main()
