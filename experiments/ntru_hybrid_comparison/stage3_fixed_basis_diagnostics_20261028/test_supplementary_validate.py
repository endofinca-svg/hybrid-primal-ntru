"""Hand-written exact arithmetic tests, no main-instance or decoder jobs."""
import unittest
from unittest.mock import patch
from . import supplementary_validate as proof
from experiments.ntru_hybrid_comparison.tests.test_reconstruction import manual_fixture


class SupplementaryProofTests(unittest.TestCase):
    def test_exact_int64_with_signed_cancellation_and_bound(self):
        product=proof.ExactProduct([[3,-5],[7,11]])
        self.assertEqual(product.multiply([[2,-3],[-4,5]]),[[-15,-43],[23,75]])
        self.assertEqual(product.proof()['row_counts'],{'proved_safe_int64_rows':2})
        self.assertLessEqual(product.proof()['maximum_proved_absolute_accumulation_bound'],proof.INT64_MAX)

    def test_huge_coefficient_uses_bigint_without_wrap(self):
        product=proof.ExactProduct([[2,3],[-5,7]])
        large=1<<90
        self.assertEqual(product.multiply([[large,-large]]),[[7*large,-4*large]])
        self.assertEqual(product.proof()['row_counts'],{'python_bigint_fallback_rows':1})

    def test_huge_matrix_uses_bigint_and_preserves_exact_value(self):
        large=1<<80;product=proof.ExactProduct([[large,1],[0,1]])
        self.assertEqual(product.multiply([[1,2]]),[[large,3]])
        self.assertEqual(product.proof()['row_counts'],{'python_bigint_fallback_rows':1})

    def test_cancellation_does_not_excuse_unproved_partial_sum_range(self):
        product=proof.ExactProduct([[2,0],[-2,1]])
        value=proof.INT64_MAX
        self.assertEqual(product.multiply([[value,value]]),[[0,value]])
        self.assertEqual(product.proof()['row_counts'],{'python_bigint_fallback_rows':1})

    def test_product_rejects_bool_and_wrong_dimension(self):
        product=proof.ExactProduct([[1,0],[0,1]])
        for row in ([True,1],[1],[1.0,2]):
            with self.assertRaises(ValueError):product.multiply([row])

    def test_norm_mismatch_is_complete_rejection_without_inverse_or_ring(self):
        public,_,_=manual_fixture()
        with patch.object(proof,'independently_validate',side_effect=AssertionError('must not call')):
            result=proof.norm_first_validation(public,[0]*8,[0]*8)
        self.assertFalse(result['public_valid']);self.assertFalse(result['full_public_verifier_called'])

    def test_correct_norm_calls_full_public_verifier_and_checks_relation(self):
        public,f,g=manual_fixture()
        self.assertTrue(proof.norm_first_validation(public,f,g)['public_valid'])
        wrong=[-v for v in g]
        result=proof.norm_first_validation(public,f,wrong)
        self.assertTrue(result['full_public_verifier_called']);self.assertFalse(result['public_valid'])


if __name__=='__main__':unittest.main()
