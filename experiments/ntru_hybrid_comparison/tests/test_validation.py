"""Public acceptance risks, using hand-written small algebra examples only."""

import copy
import unittest

from experiments.ntru_hybrid_comparison.validation import (
    PublicConstraints,
    validate_public_candidate,
)

_UNSET = object()


def public_instance(n=8, q=17, weights=(2, 2), h=None):
    return {
        "schema": "ntru-e2e-public-v1", "instance_id": "handwritten-validation",
        "n": n, "q": q, "ring": "x^n+1",
        "h": list(h) if h is not None else [1, q - 1] + [0] * (n - 2),
        "distribution": {"kind": "fixed_weight_ternary", "w_f": weights[0], "w_g": weights[1]},
    }


class PublicValidationTests(unittest.TestCase):
    def setUp(self):
        # (1-x)(1+x)=1-x^2, in a hand-written invertible example.
        self.public = public_instance()
        self.f = [1, 1, 0, 0, 0, 0, 0, 0]
        self.g = [1, 0, -1, 0, 0, 0, 0, 0]

    def validate(self, f=_UNSET, g=_UNSET, **kwargs):
        return validate_public_candidate(
            self.public, self.f if f is _UNSET else f, self.g if g is _UNSET else g, **kwargs
        )

    def test_handwritten_pair_satisfies_all_public_checks(self):
        result = self.validate()
        self.assertTrue(result["public_valid"], result)
        self.assertEqual(result["rejection_reasons"], [])
        self.assertEqual(result["norm_sq"], 4)
        self.assertTrue(all(result["checks"].values()))

    def test_zero_pair_is_not_valid_homogeneous_solution(self):
        result = self.validate([0] * 8, [0] * 8)
        self.assertTrue(result["checks"]["public_key_relation"])
        self.assertFalse(result["public_valid"])
        self.assertIn("zero_pair", result["rejection_reasons"])

    def test_wrong_weight_is_rejected_even_when_equation_holds(self):
        result = self.validate([1] + [0] * 7, [1, -1] + [0] * 6)
        self.assertTrue(result["checks"]["public_key_relation"])
        self.assertFalse(result["checks"]["weights"])
        self.assertFalse(result["public_valid"])

    def test_correct_norm_does_not_replace_ternary_or_weight_checks(self):
        public = public_instance(weights=(4, 4), h=[1] + [0] * 7)
        f = [2] + [0] * 7
        result = validate_public_candidate(public, f, f)
        self.assertTrue(result["checks"]["norm_sq"])
        self.assertTrue(result["checks"]["public_key_relation"])
        self.assertFalse(result["checks"]["ternary"])
        self.assertFalse(result["checks"]["weights"])
        self.assertFalse(result["public_valid"])

    def test_wrong_relation_rejected_with_correct_norm_weights_and_ternary(self):
        result = self.validate(g=[1, 0, 1, 0, 0, 0, 0, 0])
        for name in ("norm_sq", "weights", "ternary", "invertible"):
            self.assertTrue(result["checks"][name])
        self.assertFalse(result["public_valid"])
        self.assertIn("public_key_relation_mismatch", result["rejection_reasons"])

    def test_length_and_integer_type_errors_are_structured(self):
        for invalid in (self.f[:-1], self.f + [0], None, {}, "11000000"):
            with self.subTest(value=invalid):
                self.assertFalse(self.validate(f=invalid)["public_valid"])
        for invalid in (True, 1.0, "1"):
            f = list(self.f)
            f[0] = invalid
            with self.subTest(coefficient=invalid):
                result = self.validate(f=f)
                self.assertFalse(result["public_valid"])
                self.assertIn("candidate_coefficients_must_be_integers", result["rejection_reasons"])

    def test_invertibility_is_checked_and_can_only_be_disabled_explicitly(self):
        # x^3+1 has factor x+1.  Both vectors obey h=1 but f is nonunit.
        public = public_instance(n=3, q=7, h=[1, 0, 0])
        f = [1, 1, 0]
        required = validate_public_candidate(public, f, f)
        self.assertTrue(required["checks"]["public_key_relation"])
        self.assertFalse(required["public_valid"])
        self.assertIn("f_not_invertible", required["rejection_reasons"])
        alternative = validate_public_candidate(public, f, f, constraints=PublicConstraints(require_invertible=False))
        self.assertTrue(alternative["public_valid"])
        self.assertIsNone(alternative["checks"]["invertible"])
        self.assertEqual(alternative["invertibility_status"], "not_required")

    def test_optional_norm_constraint_is_independent_of_other_checks(self):
        result = self.validate(constraints=PublicConstraints(expected_norm_sq=36))
        self.assertFalse(result["public_valid"])
        self.assertEqual(result["norm_sq"], 4)
        self.assertEqual(result["rejection_reasons"], ["norm_sq_mismatch"])

    def test_malformed_public_metadata_does_not_reach_ring_operations(self):
        for field, value in (("n", True), ("q", 2), ("q", 17.0), ("ring", "x^n-1"), ("h", [0] * 7)):
            public = copy.deepcopy(self.public)
            public[field] = value
            with self.subTest(field=field, value=value):
                result = validate_public_candidate(public, self.f, self.g)
                self.assertFalse(result["public_valid"])
                self.assertIn("invalid_public_instance", result["rejection_reasons"])
                self.assertIsNone(result["checks"]["public_key_relation"])
        public = copy.deepcopy(self.public)
        public["distribution"]["w_f"] = True
        self.assertFalse(validate_public_candidate(public, self.f, self.g)["public_valid"])

    def test_candidate_and_metadata_are_not_mutated(self):
        before = copy.deepcopy((self.public, self.f, self.g))
        self.validate()
        self.assertEqual((self.public, self.f, self.g), before)

    def test_invalid_constraints_raise_explicit_errors(self):
        for kwargs in ({"require_invertible": 1}, {"expected_norm_sq": -1}, {"expected_norm_sq": True}, {"expected_norm_sq": 4.0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                PublicConstraints(**kwargs)
        with self.assertRaises(ValueError):
            self.validate(constraints={"require_invertible": False})


if __name__ == "__main__":
    unittest.main()
