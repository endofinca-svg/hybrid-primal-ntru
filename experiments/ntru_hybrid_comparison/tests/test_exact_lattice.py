"""Integer certificate checks independent of any lattice backend."""

import copy
import itertools
import unittest

from experiments.ntru_hybrid_comparison.exact_lattice import (
    exact_determinant,
    verify_unimodular_transform,
)


def multiply(left, right):
    return [[sum(a * b for a, b in zip(row, column))
             for column in zip(*right)] for row in left]


def permutation_determinant(matrix):
    """Small independent Leibniz-formula oracle, with no elimination."""
    total = 0
    for permutation in itertools.permutations(range(len(matrix))):
        sign = (-1) ** sum(permutation[i] > permutation[j]
                           for i in range(len(matrix))
                           for j in range(i + 1, len(matrix)))
        product = sign
        for row, column in enumerate(permutation):
            product *= matrix[row][column]
        total += product
    return total


class ExactLatticeTests(unittest.TestCase):
    def test_bareiss_matches_independent_determinants_and_does_not_mutate(self):
        matrices = (
            [[-7]],
            [[0, 1], [1, 0]],
            [[0, 0, 3], [2, 0, 4], [0, 5, 6]],
            [[3, 1, -8], [6, 2, -16], [4, 1, 3]],
            [[2, -1, 5, 3], [0, 0, 3, -1], [6, 2, 1, 4], [-3, 2, 0, 1]],
        )
        for matrix in matrices:
            with self.subTest(matrix=matrix):
                before = copy.deepcopy(matrix)
                self.assertEqual(exact_determinant(matrix), permutation_determinant(matrix))
                self.assertEqual(matrix, before)

    def test_elementary_unimodular_row_operations_and_composition(self):
        original = [[17, 0, 0], [2, 17, 0], [5, -3, 1]]
        transforms = (
            [[0, 1, 0], [1, 0, 0], [0, 0, 1]],  # Row swap.
            [[1, 0, 0], [-37, 1, 0], [0, 0, 1]],  # Add row multiple.
            [[1, 0, 0], [0, -1, 0], [0, 0, 1]],  # Negation.
        )
        combined = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
        for transform in transforms:
            with self.subTest(transform=transform):
                reduced = multiply(transform, original)
                before = copy.deepcopy((original, reduced, transform))
                result = verify_unimodular_transform(original, reduced, transform)
                self.assertTrue(result["verified"], result)
                self.assertEqual(result["dimension"], 3)
                self.assertIn(result["determinant"], (-1, 1))
                self.assertEqual((original, reduced, transform), before)
            combined = multiply(transform, combined)
        self.assertTrue(verify_unimodular_transform(
            original, multiply(combined, original), combined)["verified"])

    def test_large_integers_above_double_precision_are_exact(self):
        huge = 2 ** 100 + 17
        original = [[huge, huge + 1], [huge - 1, huge]]
        transformation = [[1, 2 ** 70 + 3], [0, -1]]
        reduced = multiply(transformation, original)
        result = verify_unimodular_transform(original, reduced, transformation)
        self.assertTrue(result["verified"], result)
        self.assertEqual(result["original_determinant"], 1)
        self.assertEqual(result["determinant"], -1)
        self.assertEqual(result["reduced_determinant"], -1)

    def test_true_index_two_sublattice_is_rejected_even_with_exact_relation(self):
        original = [[17, 0], [5, 1]]
        transformation = [[2, 0], [0, 1]]
        result = verify_unimodular_transform(
            original, multiply(transformation, original), transformation)
        self.assertFalse(result["verified"])
        self.assertTrue(result["transform_relation"])
        self.assertEqual(result["determinant"], 2)
        self.assertFalse(result["unimodular"])

    def test_wrong_transform_or_basis_is_rejected_despite_equal_determinants(self):
        original = [[17, 0], [5, 1]]
        identity = [[1, 0], [0, 1]]
        wrong_transform = [[1, 1], [0, 1]]
        for reduced, transform in (
            (original, wrong_transform),
            ([[17, 0], [6, 1]], identity),
        ):
            with self.subTest(reduced=reduced, transform=transform):
                result = verify_unimodular_transform(original, reduced, transform)
                self.assertFalse(result["verified"])
                self.assertTrue(result["unimodular"])
                self.assertFalse(result["transform_relation"])

    def test_singular_basis_and_singular_transform_are_rejected(self):
        singular = [[1, 2], [2, 4]]
        identity = [[1, 0], [0, 1]]
        result = verify_unimodular_transform(singular, singular, identity)
        self.assertFalse(result["verified"])
        self.assertFalse(result["original_full_rank"])
        self.assertTrue(result["transform_relation"])
        result = verify_unimodular_transform(identity, singular, singular)
        self.assertFalse(result["verified"])
        self.assertEqual(result["determinant"], 0)

    def test_rejects_malformed_bool_float_and_incompatible_dimensions(self):
        malformed = ([], [[1, 2]], [[1, 0], [0]], [[True]], [[1.0]],
                     [["1"]], {0: [1]}, "1")
        for matrix in malformed:
            with self.subTest(matrix=matrix):
                with self.assertRaises(ValueError):
                    exact_determinant(matrix)
                for position in range(3):
                    args = [[[1]], [[1]], [[1]]]
                    args[position] = matrix
                    result = verify_unimodular_transform(*args)
                    self.assertFalse(result["verified"])
                    self.assertTrue(result["rejection_reasons"])
        result = verify_unimodular_transform([[1]], [[1, 0], [0, 1]], [[1]])
        self.assertFalse(result["verified"])
        self.assertIn("matrix dimensions do not match", result["rejection_reasons"])


if __name__ == "__main__":
    unittest.main()
