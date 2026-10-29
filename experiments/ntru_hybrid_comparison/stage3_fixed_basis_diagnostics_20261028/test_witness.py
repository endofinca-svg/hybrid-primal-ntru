"""Small hand-written algebra fixtures; no n=128 numerical jobs."""

import copy
import importlib.util
from pathlib import Path
import unittest

from experiments.ntru_e2e.algebra import signed_rotate
from experiments.ntru_hybrid_comparison.candidates import CandidatePolicy
from experiments.ntru_hybrid_comparison.inputs import build_input_bundle
from experiments.ntru_hybrid_comparison.tests.test_reconstruction import manual_fixture

SPEC = importlib.util.spec_from_file_location("stage3_witness", Path(__file__).with_name("witness.py"))
witness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(witness)


class WitnessTests(unittest.TestCase):
    def fixture(self):
        public, f, g = manual_fixture()
        bundle = build_input_bundle(public, [1, 4], [6, 2, 5, 3], [7, 0], CandidatePolicy("full_nonzero"))
        return bundle, f, g

    def test_negacyclic_signed_rotations_positive_negative_global_sign(self):
        vector = [1, 2, 0, -3]
        self.assertEqual(witness.negacyclic_rotate(vector, 1), [3, 1, 2, 0])
        self.assertEqual(witness.negacyclic_rotate(vector, 4), [-1, -2, 0, 3])
        self.assertEqual(witness.negacyclic_rotate(vector, 8), vector)
        for power in range(-12, 13):
            self.assertEqual(witness.negacyclic_rotate(vector, power), signed_rotate(vector, power))

    def test_independent_convolution_folds_wraparound(self):
        self.assertEqual(witness.ring_product([0, 1, 0, 0], [0, 0, 0, 1]), [-1, 0, 0, 0])

    def test_witness_algebra_norm_reconstruction_and_public_validation(self):
        bundle, f, g = self.fixture()
        row = witness.check_representative(bundle, f, g, 0)
        self.assertTrue(row["public_validation"]["public_valid"])
        self.assertTrue(row["full80_covered"])
        self.assertEqual(row["norm_record"]["ambient_residual_norm_sq"], 6)
        self.assertEqual(row["norm_record"]["tail_norm_sq"], 2)
        self.assertEqual(row["norm_record"]["full_pair_norm_sq"], 8)
        self.assertFalse(row["algorithm_recovery_success"])

    def test_external_target_ids_are_exact_tail_and_order(self):
        bundle, f, g = self.fixture()
        row = witness.check_representative(bundle, f, g, 0)
        identifier = row["external_id_mapping"]["backend_integer_id"]
        self.assertEqual(bundle["id_mapping"][identifier]["u"], [-1, 1])
        self.assertEqual(bundle["targets"][identifier]["used"], row["target_used"])

    def test_corrupt_target_for_matching_identity_fails(self):
        bundle, f, g = self.fixture()
        bad = copy.deepcopy(bundle)
        identifier = next(row["target_index"] for row in bad["id_mapping"] if row["u"] == [-1, 1])
        bad["targets"][identifier]["used"][0] += 17
        with self.assertRaisesRegex(ValueError, "actual target"):
            witness.check_representative(bad, f, g, 0)

    def test_wrong_pattern_is_not_a_witness(self):
        bundle, f, g = self.fixture()
        bad = copy.deepcopy(bundle)
        bad["coordinates"]["P"] = [0, 4]
        with self.assertRaisesRegex(ValueError, "zero pattern"):
            witness.check_representative(bad, f, g, 0)

    def test_correct_norm_does_not_hide_invalid_public_relation(self):
        bundle, f, g = self.fixture()
        bad_g = [-value for value in g]
        validation = witness.independently_validate(bundle["public_instance"], f, bad_g)
        self.assertTrue(validation["checks"]["norm_sq"])
        self.assertFalse(validation["public_valid"])
        self.assertIn("public_key_relation", validation["rejection_reasons"])

    def test_state_copy_does_not_inherit_prior_mutations(self):
        bundle, _, _ = self.fixture()
        a, b = copy.deepcopy(bundle), copy.deepcopy(bundle)
        a["targets"][0]["used"][0] += 17
        a["id_mapping"][0]["u"][0] *= -1
        self.assertEqual(bundle, b)
        self.assertNotEqual(a, b)


if __name__ == "__main__":
    unittest.main()
