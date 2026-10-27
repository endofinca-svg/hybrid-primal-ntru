"""Candidate detection must reject short wrong vectors and inspect all rows."""
import unittest

from experiments.ntru_e2e.generate import generate_instance
from experiments.ntru_compare_n128_w12_w24_q97.solve_baseline import scan_rows


class CandidateDetectionTests(unittest.TestCase):
    def test_correct_pair_after_short_zero_and_wrong_weight_rows(self):
        public, truth = generate_instance(8, 257, 2, 4, 1, "fixture")
        correct = truth["g"] + truth["f"]
        wrong_weight = [1] * 6 + [0] * 10
        candidate, minimum, rejected = scan_rows([[0]*16, wrong_weight, correct], public)
        self.assertEqual(candidate["row_index"], 2)
        self.assertEqual(candidate["f"], truth["f"])
        self.assertEqual(candidate["g"], truth["g"])
        self.assertEqual(minimum, 0)
        self.assertEqual(rejected["public_validation_failed"], 1)

    def test_correct_norm_and_weights_do_not_replace_public_relation(self):
        public, truth = generate_instance(8, 257, 2, 4, 1, "fixture")
        wrong_g = list(truth["g"])
        index = next(i for i, value in enumerate(wrong_g) if value)
        wrong_g[index] *= -1
        candidate, _, rejected = scan_rows([wrong_g + truth["f"]], public)
        self.assertIsNone(candidate)
        self.assertEqual(rejected["public_validation_failed"], 1)


if __name__ == "__main__":
    unittest.main()
