"""Lightweight hand-built algebra cases; no lattice or online solver imports."""

from dataclasses import replace
import unittest

from experiments.ntru_hybrid_comparison.candidates import (
    CandidatePolicy, build_candidates, context_from_public,
)
from experiments.ntru_hybrid_comparison.offline import (
    classify_against_reference, diagnose_coverage, reference_orbit,
)
from experiments.ntru_hybrid_comparison.validation import (
    PublicConstraints, validate_public_candidate,
)


def public_fixture(n=8, weight=2):
    return {
        "schema": "ntru-e2e-public-v1", "instance_id": "hand-algebra-only",
        "n": n, "q": 17, "ring": "x^n+1", "h": [1] + [0] * (n - 1),
        "distribution": {"kind": "fixed_weight_ternary", "w_f": weight, "w_g": weight},
    }


def rotate_step(vector):
    # Independent elementary x multiplication for the orbit oracle.
    return [-vector[-1], *vector[:-1]]


class ReferenceClassificationTests(unittest.TestCase):
    def setUp(self):
        self.public = public_fixture()
        self.reference = [1, 1, 0, 0, 0, 0, 0, 0]

    def classify(self, f, g=None, **kwargs):
        return classify_against_reference(
            self.public, f, f if g is None else g,
            self.reference, self.reference, **kwargs)

    def test_identity_has_priority(self):
        result = self.classify(self.reference)
        self.assertTrue(result["public_valid"])
        self.assertEqual(result["classification"], "original")
        self.assertEqual(result["equivalent_power"], 0)

    def test_every_power_including_sign_and_wrap(self):
        current = self.reference[:]
        independent_orbit = []
        for power in range(16):
            independent_orbit.append((tuple(current), tuple(current)))
            result = self.classify(current)
            expected = "original" if power == 0 else "signed_negacyclic_equivalent"
            self.assertEqual(result["classification"], expected)
            self.assertEqual(result["equivalent_power"], power)
            self.assertTrue(result["public_valid"])
            current = rotate_step(current)
        self.assertEqual(current, self.reference)
        self.assertEqual(reference_orbit(self.public, self.reference, self.reference),
                         tuple(independent_orbit))
        # The wrap at x**7 turns the x**8 coefficient negative.
        wrapped = list(independent_orbit[7][0])
        self.assertEqual(wrapped, [-1, 0, 0, 0, 0, 0, 0, 1])

    def test_other_public_valid_support_shape(self):
        different = [1, 0, 1, 0, 0, 0, 0, 0]
        result = self.classify(different)
        self.assertTrue(result["public_valid"])
        self.assertEqual(result["classification"], "other_public_valid")
        self.assertIsNone(result["equivalent_power"])

    def test_ordinary_wrapped_rotation_is_not_negacyclic_equivalent(self):
        ordinary = self.reference[1:] + self.reference[:1]
        result = self.classify(ordinary)
        self.assertTrue(result["public_valid"])
        self.assertEqual(result["classification"], "other_public_valid")

    def test_f_and_g_must_use_same_rotation(self):
        result = self.classify(rotate_step(self.reference), self.reference)
        self.assertFalse(result["public_valid"])
        self.assertEqual(result["classification"], "invalid")
        self.assertIn("public_key_relation_mismatch", result["rejection_reasons"])

    def test_invalid_candidate_retains_validation_without_reference(self):
        zero = [0] * 8
        result = classify_against_reference(self.public, zero, zero, None, None)
        expected = validate_public_candidate(self.public, zero, zero)
        self.assertFalse(result["public_valid"])
        self.assertEqual(result["classification"], "invalid")
        self.assertEqual(result["rejection_reasons"], expected["rejection_reasons"])
        self.assertEqual(result["checks"], expected["checks"])

    def test_invalid_reference_errors_without_altering_public_validation(self):
        before = validate_public_candidate(self.public, self.reference, self.reference)
        with self.assertRaisesRegex(ValueError, "reference"):
            classify_against_reference(
                self.public, self.reference, self.reference, [0] * 8, [0] * 8)
        with self.assertRaisesRegex(ValueError, "reference"):
            reference_orbit(self.public, [1], [1])
        self.assertEqual(validate_public_candidate(self.public, self.reference, self.reference), before)

    def test_reference_change_changes_only_classification(self):
        other = [1, 0, 1, 0, 0, 0, 0, 0]
        original = self.classify(self.reference)
        relabeled = classify_against_reference(
            self.public, self.reference, self.reference, other, other)
        self.assertEqual(original["classification"], "original")
        self.assertEqual(relabeled["classification"], "other_public_valid")
        for field in validate_public_candidate(self.public, self.reference, self.reference):
            self.assertEqual(original[field], relabeled[field])

    def test_exact_orbit_pair_deduplication_in_explicit_noninvertible_model(self):
        public = public_fixture(n=3, weight=3)
        reference = [1, -1, 1]
        constraints = PublicConstraints(require_invertible=False)
        expected = ((tuple(reference), tuple(reference)),
                    (tuple(-x for x in reference), tuple(-x for x in reference)))
        self.assertEqual(reference_orbit(public, reference, reference, constraints=constraints), expected)


class CoverageTests(unittest.TestCase):
    def setUp(self):
        self.public = public_fixture()
        self.reference = [1, 1, 0, 0, 0, 0, 0, 0]

    def batch(self, tail, *, h=None, supplied=None):
        policy = CandidatePolicy("full_nonzero") if h is None else CandidatePolicy("weight_range", h)
        return build_candidates(context_from_public(self.public, tail), policy,
                                supplied_candidates=supplied)

    def diagnose(self, pattern, batch, **kwargs):
        return diagnose_coverage(self.public, self.reference, self.reference, pattern, batch, **kwargs)

    def test_incompatible_pattern(self):
        result = self.diagnose([0, 2, 4, 6], self.batch([1, 3]))
        self.assertFalse(result["pattern_compatible"])
        self.assertFalse(result["nonzero_tail_covered"])
        self.assertEqual(result["coverage_state"], "pattern_incompatible")
        self.assertIsNone(result["public_valid_found"])

    def test_compatible_but_all_tails_zero(self):
        # Only adjacent support {0,1} survives; surviving isolated coordinate 4
        # is the tail, so each compatible reference pair has the zero tail.
        result = self.diagnose([2, 3, 5, 6, 7], self.batch([4]))
        self.assertTrue(result["pattern_compatible"])
        self.assertFalse(result["nonzero_tail_covered"])
        self.assertEqual(result["coverage_state"], "all_compatible_tails_zero")
        self.assertGreater(result["zero_tail_orbit_pair_count"], 0)
        self.assertEqual(result["nonzero_tail_orbit_pair_count"], 0)

    def test_correct_nonzero_tail_excluded_by_weight_cap(self):
        result = self.diagnose([2, 3, 4, 5, 6, 7], self.batch([1, 0], h=1))
        self.assertTrue(result["pattern_compatible"])
        self.assertFalse(result["nonzero_tail_covered"])
        self.assertEqual(result["coverage_state"], "nonzero_tail_not_covered")
        self.assertEqual(result["nonzero_tail_orbit_pair_count"], 2)
        self.assertEqual(result["covered_orbit_pair_count"], 0)

    def test_compatible_nonzero_tail_covered_with_no_outputs(self):
        result = self.diagnose([2, 3, 4, 5, 6, 7], self.batch([1, 0]))
        self.assertTrue(result["pattern_compatible"])
        self.assertTrue(result["nonzero_tail_covered"])
        self.assertEqual(result["coverage_state"], "covered")
        self.assertIsNone(result["public_valid_found"])
        self.assertIsNone(result["output_count"])
        self.assertEqual(result["tail_positions"], [1, 0])

    def test_ordered_tail_coordinates_used_without_sorting(self):
        # Distinct support coordinates distinguish [0,1,4] and [4,0,1].
        # Under P, the only compatible supports are {0,1} with both signs.
        supplied = [(1, 1, 0)]
        pattern = [2, 3, 5, 6, 7]
        in_order = self.batch([0, 1, 4], supplied=supplied)
        permuted = self.batch([4, 0, 1], supplied=supplied)
        self.assertTrue(self.diagnose(pattern, in_order)["nonzero_tail_covered"])
        self.assertFalse(self.diagnose(pattern, permuted)["nonzero_tail_covered"])

    def test_actual_outputs_separate_from_reference_coverage_and_flags(self):
        batch = self.batch([1, 3])
        pattern = [0, 2, 4, 6]  # No compatible reference rotation.
        empty = self.diagnose(pattern, batch, outputs=[])
        self.assertFalse(empty["public_valid_found"])
        self.assertEqual(empty["output_count"], 0)
        forged = self.diagnose(pattern, batch, outputs=[
            {"f": [0] * 8, "g": [0] * 8, "public_valid": True}])
        self.assertFalse(forged["public_valid_found"])
        actual = self.diagnose(pattern, batch, outputs=[
            {"f": self.reference, "g": self.reference, "public_valid": False}])
        self.assertTrue(actual["public_valid_found"])
        self.assertFalse(actual["pattern_compatible"])
        self.assertFalse(actual["nonzero_tail_covered"])

    def test_output_pair_form_and_malformed_outputs(self):
        result = self.diagnose([], self.batch([1]), outputs=[None, (self.reference, self.reference)])
        self.assertEqual(result["output_count"], 2)
        self.assertFalse(result["output_validations"][0]["public_valid"])
        self.assertTrue(result["public_valid_found"])

    def test_mismatched_public_context_and_invalid_coordinates_rejected(self):
        batch = self.batch([1, 0])
        other_public = dict(self.public, instance_id="different-hand-fixture")
        with self.assertRaisesRegex(ValueError, "context"):
            diagnose_coverage(other_public, self.reference, self.reference, [], batch)
        for positions in ([1], [8], [-1], [True], [2, 2]):
            with self.subTest(positions=positions), self.assertRaises(ValueError):
                self.diagnose(positions, batch)

    def test_forged_batch_dimension_zero_and_duplicate_not_silently_accepted(self):
        batch = self.batch([1, 0])
        candidate = batch.entries[0]
        invalid_entries = (
            (replace(candidate, u=(1,)),),
            (replace(candidate, u=(0, 0)),),
            (candidate, candidate),
        )
        for entries in invalid_entries:
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                self.diagnose([], replace(batch, entries=entries))

    def test_coverage_counts_distinct_joint_representatives(self):
        public = public_fixture(n=3, weight=3)
        reference = [1, -1, 1]
        batch = build_candidates(context_from_public(public, [2]), CandidatePolicy("full_nonzero"))
        result = diagnose_coverage(public, reference, reference, [], batch,
                                   constraints=PublicConstraints(require_invertible=False))
        self.assertEqual(result["unique_orbit_pair_count"], 2)
        self.assertEqual(result["compatible_orbit_pair_count"], 2)
        self.assertEqual(result["covered_orbit_pair_count"], 2)


if __name__ == "__main__":
    unittest.main()
