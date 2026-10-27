"""Public pattern determinism; no backend, historical solver, or truth read."""

from hashlib import sha256
import random
import unittest

from experiments.ntru_hybrid_comparison.patterns import select_pattern


class PatternSelectionTests(unittest.TestCase):
    def test_first_three_historical_patterns_match_saved_public_sequence(self):
        expected = [
            [0, 1, 6, 9, 18, 25, 26, 29, 30, 31, 35, 36, 39, 42, 45, 46, 47, 51, 52, 54,
             55, 59, 65, 69, 74, 76, 88, 90, 94, 96, 99, 105, 106, 111, 115, 118, 119, 122, 123, 127],
            [0, 5, 8, 16, 18, 19, 27, 28, 30, 32, 33, 34, 37, 45, 47, 48, 51, 55, 58, 66,
             68, 70, 72, 80, 84, 87, 88, 90, 94, 96, 101, 105, 110, 113, 119, 122, 123, 124, 126, 127],
            [0, 2, 3, 5, 6, 9, 13, 14, 17, 20, 21, 25, 27, 31, 33, 38, 41, 48, 50, 51,
             52, 57, 59, 60, 72, 77, 81, 82, 87, 89, 90, 94, 100, 103, 106, 110, 116, 117, 119, 125],
        ]
        for index, wanted in enumerate(expected):
            with self.subTest(index=index):
                self.assertEqual(wanted, select_pattern(128, 40, 10001, index, "historical_sha256_shuffle_v1"))

    def test_historical_digest_uses_big_endian_full_sha256_and_explicit_seed(self):
        for seed in (0, 10001, 20261027):
            for index in (0, 1, 2):
                material = "pattern-primal-v1:" + str(seed) + ":" + str(index)
                permutation = list(range(31))
                rng = random.Random(int.from_bytes(sha256(material.encode("ascii")).digest(), "big"))
                rng.shuffle(permutation)
                self.assertEqual(sorted(permutation[:11]),
                                 select_pattern(31, 11, seed, index, "historical_sha256_shuffle_v1"))

    def test_random_sample_rule_preserves_original_sequential_rng(self):
        for n, k, seed in ((128, 40, 10001), (8, 2, 123), (5, 0, 0), (5, 5, 9)):
            rng = random.Random(seed)
            for index in range(5):
                self.assertEqual(sorted(rng.sample(range(n), k)),
                                 select_pattern(n, k, seed, index, "random_sample_v1"))

    def test_b_e_d_repeated_calls_have_identical_public_pattern(self):
        for rule in ("random_sample_v1", "historical_sha256_shuffle_v1"):
            for index in range(3):
                first = select_pattern(128, 40, 10001, index, rule)
                self.assertEqual(first, select_pattern(128, 40, 10001, index, rule))
                self.assertEqual(first, sorted(set(first)))
                self.assertEqual(40, len(first))

    def test_duplicate_sets_are_not_skipped_or_filtered(self):
        for rule in ("random_sample_v1", "historical_sha256_shuffle_v1"):
            self.assertEqual([0, 1, 2, 3], select_pattern(4, 4, 10001, 0, rule))
            self.assertEqual([0, 1, 2, 3], select_pattern(4, 4, 10001, 1, rule))
            self.assertEqual([], select_pattern(4, 0, 10001, 2, rule))

    def test_invalid_public_parameters_are_rejected(self):
        invalid = [(0, 0, 1, 0), (4, -1, 1, 0), (4, 5, 1, 0), (4, 2, -1, 0),
                   (4, 2, 1, -1), (True, 0, 1, 0), (4, True, 1, 0), (4, 2, True, 0),
                   (4, 2, 1, True), (4.0, 2, 1, 0), (4, 2, 1, "0")]
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValueError):
                select_pattern(*values, "random_sample_v1")
        with self.assertRaises(ValueError):
            select_pattern(4, 2, 1, 0, "reference_selected")


if __name__ == "__main__":
    unittest.main()
